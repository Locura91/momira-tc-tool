from typing import Dict, Any, List
from pydantic import ValidationError
from schemas import HumanPreConfig, ContractClosedTourVO, build_datasheets, DatasheetEN, ItineraryItem, ContractClosedTourOptionVO, WEEKDAY_NAMES, SupplementVO, SupplementPriceVO, SupplementTranslation, OptionTranslation
from schemas import TicketHumanPreConfig, ApiStaticContentTicketVO, ContractTicketModalityVO, GeolocationVO, MeetingPointVO, TicketDatasheetEN, TicketCancellationRange, TicketSupplementVO, TicketSupplementTranslation, TicketRemark
from api_client import TravelCompositorAPI
from geocoding_client import geocode

DEFAULT_MEETING_POINT = ("Meet your guide in the airport arrival hall or, if you are already in the "
                          "tour's starting city, in your hotel lobby.")


def normalize_time_hhmmss(value: str) -> str:
    """
    CONFIRMED via a real API error: Travel Compositor's startTime/endTime
    fields are java.time.LocalTime and require HH:MM:SS - "12:00" alone
    fails with a DateTimeParseException. This guarantees the right format
    regardless of what was extracted or typed in (HH:MM -> HH:MM:SS,
    already-correct HH:MM:SS passes through unchanged, empty stays empty).
    """
    value = (value or "").strip()
    if not value:
        return ""
    parts = value.split(":")
    if len(parts) == 2:
        return f"{value}:00"
    if len(parts) == 3:
        return value
    return value  # malformed input - pass through, let the API's own validation catch it clearly


# CONFIRMED business rule (human instruction, 2026-07-30): in Indonesia
# specifically, NO excursion or tour may ever start on Vesak Day (Hari Raya
# Waisak) - it must always default to a blocked stop-sale date, regardless
# of what the source document's own schedule says. Vesak Day follows the
# Buddhist lunar calendar so it moves every year and can't be computed with
# a simple formula - these are the real, confirmed government-recognized
# dates for the years currently published. Sourced from publicholidays.co.id
# (checked 2026-07-30). Extend this table as later years become official.
VESAK_DAY_DATES = {
    2026: "2026-05-31",
    2027: "2027-05-20",
    2028: "2028-05-09",
}


def _is_indonesia_country_value(country_value) -> bool:
    """
    Matches Travel Compositor's own 'country' field on a DestinationVO,
    which may hold either an ISO code ("ID") or a full name ("Indonesia")
    depending on account/version - check both rather than betting on one.
    """
    if not country_value:
        return False
    value = str(country_value).strip().lower()
    return value == "id" or "indonesia" in value


def _is_indonesia_place_name(display_name: str) -> bool:
    return bool(display_name) and "indonesia" in display_name.lower()


def _is_indonesia_destination(place_name: str, api_client: TravelCompositorAPI = None) -> bool:
    """
    Determines whether a single place name is in Indonesia. Prefers Travel
    Compositor's OWN destination data (the 'country' field on DestinationVO,
    already cached via the same lookup ClosedTour destination-resolution
    uses) since it's the authoritative, official source - falls back to the
    free OpenStreetMap/Nominatim geocoder (same one used for Ticket
    coordinates, cached) only when Travel Compositor has no record for that
    place, since its own destination list won't cover every small town a
    DMC document might mention.
    """
    if not place_name:
        return False
    if api_client is not None:
        try:
            country = api_client.get_destination_country(place_name)
        except Exception:
            country = None
        if country is not None:
            return _is_indonesia_country_value(country)
    geo_result = geocode(place_name)
    return geo_result.get("valid") and _is_indonesia_place_name(geo_result.get("display_name"))


def _detect_indonesia_tour(raw_locations: List[str], api_client: TravelCompositorAPI = None) -> bool:
    """
    Best-effort check for whether a ClosedTour's itinerary is in Indonesia -
    checks each raw destination name (Travel Compositor's own country data
    first, OpenStreetMap as fallback - see _is_indonesia_destination) and
    stops as soon as one resolves to a place inside Indonesia.
    """
    for loc_name in raw_locations:
        if loc_name and _is_indonesia_destination(loc_name, api_client):
            return True
    return False


def resolve_release_days(default_days: int, mentioned_days: List[Any]) -> int:
    """
    Human instruction (2026-07-30): the release period (how many days before
    departure a tour/ticket becomes bookable) defaults to whatever the human
    set in the pre-config (usually 30) - UNLESS the source document itself
    mentions an explicit booking/reservation deadline, in which case that
    ALWAYS wins over the default. If the document mentions more than one
    (e.g. different components have different notice periods), use the
    HIGHER one, since a longer required-notice period is the safer choice -
    it never turns away a booking too late, only ever asks for it earlier.
    `mentioned_days` is whatever the AI extraction put in
    "release_days_mentions" - defensively coerced/filtered here since it's
    AI-produced and could contain non-numeric junk or non-positive values.
    """
    valid_mentions = []
    for value in (mentioned_days or []):
        try:
            n = int(value)
        except (TypeError, ValueError):
            continue
        if n > 0:
            valid_mentions.append(n)
    if valid_mentions:
        return max(valid_mentions)
    return default_days


def vesak_day_stop_sales() -> List[Dict[str, str]]:
    """
    Every known Vesak Day date as a {"start", "end"} stop-sale entry (same
    single day for both). Safe to always include every known year regardless
    of the product's actual selling window - a stop-sale date outside the
    real range is simply unused, never harmful.
    """
    return [{"start": d, "end": d} for d in VESAK_DAY_DATES.values()]


def vesak_day_coverage_note() -> str:
    """
    Plain-language note for the UI so a human reviewing an Indonesia product
    can see at a glance how far the automatic Vesak Day block reaches, and
    knows to add later years manually once Indonesia officially confirms them.
    """
    years = sorted(VESAK_DAY_DATES.keys())
    return (f"Vesak Day is automatically blocked for {years[0]}-{years[-1]} (confirmed dates). "
            f"For years beyond {years[-1]}, Indonesia's Vesak Day date isn't officially "
            f"confirmed yet - add it manually as a stop-sale once announced.")


def _merge_stop_sales(existing: List[Dict[str, str]], additions: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Merges two stop-sale lists, skipping any addition that's already present (same start+end)."""
    existing = list(existing or [])
    existing_keys = {(s.get("start"), s.get("end")) for s in existing if isinstance(s, dict)}
    for item in additions:
        key = (item.get("start"), item.get("end"))
        if key not in existing_keys:
            existing.append(item)
            existing_keys.add(key)
    return existing


def build_closed_tour_payloads(
    pre_config: HumanPreConfig,
    extracted_dmc_data: Dict[str, Any],
    api_client: TravelCompositorAPI
) -> Dict[str, Any]:
    """
    Combines Human Pre-Configuration + AI Extracted DMC data + Destination Lookup
    to create both the Main Tour and Closed Tour Option payloads.
    """

    # 1. Resolve Destination Codes via Travel Compositor API
    validated_itinerary: List[ItineraryItem] = []
    unresolved_destinations: List[str] = []
    itinerary_resolution: List[Dict[str, Any]] = []  # per-item status for clean UI display
    raw_locations = extracted_dmc_data.get("itinerary_destinations", [])

    for loc_name in raw_locations:
        result = api_client.resolve_destination(loc_name)
        if not result["valid"]:
            # Flag it instead of silently uploading a made-up code.
            unresolved_destinations.append(loc_name)
        itinerary_resolution.append({
            "input": loc_name,
            "destination": result["tc_code"],
            "resolved_name": result.get("name"),
            "valid": result["valid"],
        })
        validated_itinerary.append(
            ItineraryItem(
                description={},
                destination=result["tc_code"],
                hotelsId=[]
            )
        )

    # CONFIRMED Travel Compositor validation rule: the same destination CANNOT
    # appear more than once CONSECUTIVELY (non-consecutive repeats, e.g. a tour
    # returning to its starting city later, are fine and required to stay).
    # Collapse any back-to-back duplicate stops - e.g. two consecutive
    # overnight days at the same place should be ONE itinerary entry, not two.
    collapsed_itinerary: List[ItineraryItem] = []
    for item in validated_itinerary:
        if collapsed_itinerary and collapsed_itinerary[-1].destination == item.destination:
            continue  # skip - same as the immediately preceding stop
        collapsed_itinerary.append(item)
    validated_itinerary = collapsed_itinerary

    # Indonesia / Vesak Day rule (human instruction): tours in Indonesia can
    # never start on Vesak Day - automatically block it as a stop-sale below.
    is_indonesia = _detect_indonesia_tour(raw_locations, api_client)

    # Transports = number of destination CHANGES along the itinerary (not total stops)
    transports_count = sum(
        1 for i in range(1, len(validated_itinerary))
        if validated_itinerary[i].destination != validated_itinerary[i - 1].destination
    )

    # Hotels = number of DIFFERENT accommodations the client actually stays in.
    # Prefer the AI's own count (it understands e.g. "same cruise ship the whole
    # way" = 1 hotel, not one per destination). Fall back to counting unique
    # destinations only if that field wasn't provided at all.
    if "hotels_count" in extracted_dmc_data:
        hotels_count = extracted_dmc_data["hotels_count"]
    else:
        hotels_count = len(set(item.destination for item in validated_itinerary if item.destination))

    # Convert the simple flat supplement table into the confirmed real
    # SupplementVO structure. A flat per-person price applies to both single
    # and double occupancy (the common real-world pattern - e.g. an optional
    # dinner costs the same whether traveling solo or as a couple); triple/
    # quadruple stay at 0 unless a future refinement adds per-occupancy input.
    supplements_list = []
    for s in extracted_dmc_data.get("supplements", []):
        price_val = float(s.get("price", 0) or 0)
        # NOTE: the confirmed schema's singlePrice/doublePrice/etc are inherently
        # per-person amounts (that's what "per occupancy" means in this API).
        # "Per Pax" unchecked is tracked for the human's own clarity, but we don't
        # have a confirmed API field for a genuinely flat/non-per-pax supplement
        # charge - if you need that, verify with Travel Compositor directly.
        travel_windows = []
        if s.get("travel_start_date") and s.get("travel_end_date"):
            travel_windows = [{"start": s["travel_start_date"], "end": s["travel_end_date"]}]

        supplements_list.append(SupplementVO(
            translations={"EN": SupplementTranslation(name=s.get("name", ""))},
            price=SupplementPriceVO(singlePrice=price_val, doublePrice=price_val),
            mandatory=bool(s.get("mandatory", False)),
            onRequest=bool(s.get("on_request", False)),
            free=(price_val == 0),
            travelWindows=travel_windows,
        ))

    # 2. Build Main Tour Payload (ContractClosedTourVO)
    # NOTE: these fields are required `str`/`list` types on DatasheetEN /
    # ContractClosedTourVO. `.get(key, default)` only falls back to `default`
    # when the key is ABSENT - if the AI extraction explicitly returned
    # `None`, `.get()` still returns None and pydantic raises a
    # ValidationError. `or <fallback>` below guards against that (same class
    # of bug that used to crash the Ticket path unguarded - see
    # build_ticket_payloads below).
    datasheet_en = DatasheetEN(
        name=extracted_dmc_data.get("tour_name") or "",
        description=extracted_dmc_data.get("description") or "",
        hotels=extracted_dmc_data.get("hotels_text") or "",
        voucherRemarks="",
        included=extracted_dmc_data.get("included") or "",
        excluded=extracted_dmc_data.get("excluded") or "",
        meetingPoint=extracted_dmc_data.get("meeting_point") or DEFAULT_MEETING_POINT,
        remarksTitle="Policy",
        remarksDescription=extracted_dmc_data.get("policy_remarks") or ""
    )

    effective_release_days = resolve_release_days(
        pre_config.days_available_before_release, extracted_dmc_data.get("release_days_mentions")
    )

    main_tour = ContractClosedTourVO(
        supplier=pre_config.supplier_code or pre_config.supplier_id,
        userId=pre_config.user_id,
        code=extracted_dmc_data.get("tour_code") or f"TOUR-{pre_config.provider_code}",
        providerCode=pre_config.provider_code,
        name=extracted_dmc_data.get("tour_name") or "",
        datasheets=build_datasheets(datasheet_en),
        images=extracted_dmc_data.get("image_urls", []),
        itinerary=validated_itinerary,
        transports=transports_count,
        hotels=hotels_count,
        startTime=normalize_time_hhmmss(extracted_dmc_data.get("start_time", "")),
        endTime=normalize_time_hhmmss(extracted_dmc_data.get("end_time", "")),
        supplements=supplements_list,
        minChildAge=extracted_dmc_data.get("min_child_age", pre_config.min_child_age),
        maxChildAge=extracted_dmc_data.get("max_child_age", pre_config.max_child_age),
        currency=pre_config.currency,
        nights=extracted_dmc_data.get("nights", 1),
        minPax=pre_config.min_pax,
        maxPax=pre_config.max_pax,
        modalityCodes=[pre_config.modality_code],
        daysAvailableBeforeRelease=effective_release_days,
        active=False  # LOCKED: Strictly upload as inactive/draft
    )

    # 3. Build Closed Tour Option Payload (ContractClosedTourOptionVO)
    # NOTE: priceList is required by the API, but we don't want to hard-crash
    # here during a preview/dry-run before pricing has been entered. Catch
    # the validation error and surface it as data instead; the actual
    # publish step (in web_extractor.py) still refuses to upload if this
    # error is present.
    combined_stop_sales = extracted_dmc_data.get("stop_sales", []) or []
    if is_indonesia:
        combined_stop_sales = _merge_stop_sales(combined_stop_sales, vesak_day_stop_sales())

    tour_option_payload = None
    tour_option_error = None
    try:
        tour_option = ContractClosedTourOptionVO(
            code=pre_config.modality_code,
            operationalDays=extracted_dmc_data.get("operational_days", WEEKDAY_NAMES.copy()),
            stopSales=combined_stop_sales,
            priceList=sorted(extracted_dmc_data.get("price_list", []), key=lambda p: p.get("startDate", "")),
            translations={"EN": OptionTranslation(name=pre_config.modality_code, remarks=None)},
            onRequest=pre_config.on_request,
            quantityPerDay=99,
            useAdditionalOnRequestQuota=False
        )
        tour_option_payload = tour_option.dict()
    except ValidationError as e:
        tour_option_error = str(e)

    return {
        "supplier_id": pre_config.supplier_id,
        "main_tour_code": main_tour.code,
        "main_tour_payload": main_tour.dict(),
        "tour_option_payload": tour_option_payload,
        "tour_option_error": tour_option_error,
        "unresolved_destinations": unresolved_destinations,  # surface these in the Review UI before publishing
        "itinerary_resolution": itinerary_resolution,  # per-item status for clean green/red UI display
        "is_indonesia": is_indonesia,
        "vesak_day_note": vesak_day_coverage_note() if is_indonesia else None,
        "effective_release_days": effective_release_days,
        "release_days_overridden": effective_release_days != pre_config.days_available_before_release,
    }

def build_ticket_payloads(
    pre_config: TicketHumanPreConfig,
    extracted_ticket_data: Dict[str, Any],
    api_client: TravelCompositorAPI
) -> Dict[str, Any]:
    """
    Mirrors build_closed_tour_payloads but for Tickets (excursions - single
    destination, no overnight). Key structural differences, confirmed
    against real data:
      - ONE geolocation (lat/long) instead of a resolved itinerary list
      - Pricing is per PASSENGER TYPE (adult/child/infant), not room occupancy
      - Each Modality holds ONE price + ONE date range, not a seasonal array -
        seasonal/holiday pricing goes through dated Supplements instead
      - Supplements use a different shape (adult/child/infant price + dates)
    """
    city = extracted_ticket_data.get("city", "")
    manual_lat = extracted_ticket_data.get("manual_latitude")
    manual_lng = extracted_ticket_data.get("manual_longitude")
    if manual_lat is not None and manual_lng is not None:
        geoloc = {"latitude": float(manual_lat), "longitude": float(manual_lng), "name": city, "valid": True, "source": "manual override"}
    else:
        geo_result = geocode(city)
        geoloc = {
            "latitude": geo_result["latitude"], "longitude": geo_result["longitude"],
            "name": geo_result.get("display_name") or city, "valid": geo_result["valid"],
            "source": "OpenStreetMap/Nominatim" if geo_result["valid"] else "not_found",
        }

    # Indonesia / Vesak Day rule (human instruction): excursions in Indonesia
    # can never start on Vesak Day - automatically block it as a stop-sale
    # below. Prefers Travel Compositor's own destination country data, falls
    # back to the OpenStreetMap lookup already done above for coordinates.
    is_indonesia = _is_indonesia_destination(city, api_client)

    # Resolve each meeting point's own coordinates; fall back to the main
    # city's coordinates if a specific meeting point can't be resolved on
    # its own (e.g. "Tokyo Station" not being a distinct destination record),
    # or if it's explicitly a variable/guest-specific location (e.g. "pick up
    # from your hotel") that was never a real geocodable place to begin with.
    meeting_points_out = []
    for mp in extracted_ticket_data.get("meeting_points", []):
        if isinstance(mp, dict):
            mp_desc = mp.get("description", "")
            is_variable = bool(mp.get("variable_location", False))
        else:
            mp_desc, is_variable = str(mp), False

        if is_variable:
            lat, lng = geoloc.get("latitude"), geoloc.get("longitude")
        else:
            mp_geo = geocode(f"{mp_desc}, {city}" if city else mp_desc)
            lat = mp_geo["latitude"] if mp_geo["valid"] else geoloc.get("latitude")
            lng = mp_geo["longitude"] if mp_geo["valid"] else geoloc.get("longitude")
        if lat is not None and lng is not None:
            meeting_points_out.append(MeetingPointVO(description=mp_desc, latitude=lat, longitude=lng))

    # Convert supplements (ticket-specific shape: per-passenger-type + dates)
    supplements_list = []
    for s in extracted_ticket_data.get("supplements", []):
        supplements_list.append(TicketSupplementVO(
            adultPriceSupplement=float(s.get("adult_price", 0) or 0),
            childrenPriceSupplement=float(s.get("children_price", 0) or 0),
            infantPriceSupplement=float(s.get("infant_price", 0) or 0),
            startDate=s.get("travel_start_date", ""),
            endDate=s.get("travel_end_date", ""),
            translations={"EN": TicketSupplementTranslation(name=s.get("name", ""))},
        ))

    # Filter out any None/blank/literal-"None" garbage BEFORE normalizing -
    # confirmed via a real API error that a stray "None" string (from a blank
    # data_editor row upstream getting str()'d) reaches here and blows up
    # java.time.LocalTime deserialization server-side with a raw
    # DateTimeParseException. Also normalize HH:MM -> HH:MM:SS like
    # start_time/end_time already do (see normalize_time_hhmmss above).
    time_tables_list = [
        normalize_time_hhmmss(t) for t in (extracted_ticket_data.get("time_tables", []) or [])
        if t and str(t).strip() and str(t).strip().lower() not in ("none", "nan")
    ]

    effective_release_days = resolve_release_days(
        pre_config.days_available_before_release, extracted_ticket_data.get("release_days_mentions")
    )

    main_ticket_error = None
    main_ticket_payload = None
    try:
        # NOTE: TicketDatasheetEN.name/description are required `str` fields.
        # `.get(key, default)` only applies the default when the key is ABSENT -
        # if the AI extraction explicitly returned `None` for a field, `.get()`
        # still returns None and pydantic raises ValidationError. That crash
        # used to happen here, outside any try/except, and took down the whole
        # batch/app. It's now caught below (via `or ""`  defensive coercion
        # plus this try block), and reported as a per-item error instead.
        datasheet_en = TicketDatasheetEN(
            name=extracted_ticket_data.get("ticket_name") or "",
            description=extracted_ticket_data.get("description") or "",
            meetingPoint=extracted_ticket_data.get("meeting_point_summary") or "Hotel Lobby",
            departureTime=time_tables_list[0] if time_tables_list else "",
            voucherRemarks=extracted_ticket_data.get("voucher_remarks") or "",
            includes=extracted_ticket_data.get("includes") or [],
            excludes=extracted_ticket_data.get("excludes") or [],
            activityType=extracted_ticket_data.get("activity_type"),
        )
        main_ticket_kwargs = dict(
            code=pre_config.ticket_code,
            name=extracted_ticket_data.get("ticket_name", ""),
            geolocation=GeolocationVO(
                latitude=geoloc.get("latitude") if geoloc.get("latitude") is not None else None,
                longitude=geoloc.get("longitude") if geoloc.get("longitude") is not None else None,
            ),
            city=city,
            datasheets={"EN": datasheet_en},
            currency=pre_config.currency,
            imageUrls=extracted_ticket_data.get("image_urls", []),
            adultTaxesAmount=float(extracted_ticket_data.get("adult_taxes_amount", 0) or 0),
            childTaxesAmount=float(extracted_ticket_data.get("child_taxes_amount", 0) or 0),
            infantTaxesAmount=float(extracted_ticket_data.get("infant_taxes_amount", 0) or 0),
            daysAvailableBeforeRelease=effective_release_days,
            duration=float(extracted_ticket_data.get("duration", 0) or 0),
            durationType=extracted_ticket_data.get("duration_type", "HOURS"),
            cancellationRanges=[TicketCancellationRange()],  # LOCKED default: always 30 days / 100%, matching ClosedTour's confirmed convention
            meetingPoints=meeting_points_out,
            active=False,  # LOCKED default - same confirmed workflow as ClosedTour applies
        )
        if extracted_ticket_data.get("product_types"):
            main_ticket_kwargs["productTypes"] = extracted_ticket_data["product_types"]
        main_ticket = ApiStaticContentTicketVO(**main_ticket_kwargs)
        main_ticket_payload = main_ticket.dict()
    except ValidationError as e:
        main_ticket_error = str(e)

    ticket_option_payload = None
    ticket_option_error = None
    try:
        # Pricing is 3 mutually-exclusive modes (DISTRIBUTION/OCCUPANCY/SERVICE).
        # The API doesn't ignore the fields belonging to the two UNSELECTED
        # modes - it validates/stores whatever is sent. Historically all three
        # fields were sent unconditionally, so switching modes in the UI left
        # stale values (e.g. an old Distribution adult price) sitting in the
        # payload alongside the new Occupancy/Service data and caused
        # conflicts. Zero out the two unselected modes' fields here, based on
        # the actually-selected price_type, regardless of what's still
        # sitting in the extracted/session data.
        selected_price_type = extracted_ticket_data.get("price_type") or "OCCUPANCY"
        base_adult_price = float(extracted_ticket_data.get("base_adult_price", 0) or 0)
        base_children_price = float(extracted_ticket_data.get("base_children_price", 0) or 0)
        base_infant_price = float(extracted_ticket_data.get("base_infant_price", 0) or 0)
        base_service_price = float(extracted_ticket_data.get("base_service_price", 0) or 0)
        occupancy_prices = extracted_ticket_data.get("occupancy_prices", [])
        if selected_price_type != "DISTRIBUTION":
            # baseAdultPrice is REQUIRED on ContractTicketModalityVO regardless
            # of price mode (schemas.py: Field(...)) - confirmed the real API
            # rejects 0 here even when the actual price lives elsewhere
            # (Occupancy table / Service flat total). Use 1 as a harmless
            # nonzero placeholder rather than a real per-adult charge.
            base_adult_price = 1.0
            base_children_price = 0.0
            base_infant_price = 0.0
        if selected_price_type != "SERVICE":
            base_service_price = 0.0
        if selected_price_type != "OCCUPANCY":
            occupancy_prices = []

        combined_ticket_stop_sales = extracted_ticket_data.get("stop_sales", []) or []
        if is_indonesia:
            combined_ticket_stop_sales = _merge_stop_sales(combined_ticket_stop_sales, vesak_day_stop_sales())

        ticket_option = ContractTicketModalityVO(
            code=pre_config.modality_code,
            operationalDays=extracted_ticket_data.get("operational_days", WEEKDAY_NAMES.copy()),
            remarks={"EN": TicketRemark(name=pre_config.modality_code, remarks=None)},
            supplements=supplements_list,
            stopSales=combined_ticket_stop_sales,
            ticketsPerDay=99,
            disallowChildren=bool(extracted_ticket_data.get("disallow_children", False)),
            onRequest=pre_config.on_request,
            disallowInfant=bool(extracted_ticket_data.get("disallow_infant", False)),
            disallowAdult=bool(extracted_ticket_data.get("disallow_adult", False)),
            startDate=extracted_ticket_data.get("start_date", ""),
            endDate=extracted_ticket_data.get("end_date", ""),
            baseAdultPrice=base_adult_price,
            baseChildrenPrice=base_children_price,
            baseInfantPrice=base_infant_price,
            baseServicePrice=base_service_price,
            occupancyPrices=occupancy_prices,
            priceType=selected_price_type,
            maxPassengers=pre_config.max_passengers,
            minPassengers=pre_config.min_passengers,
            childAgeMin=extracted_ticket_data.get("child_age_min", 6),
            childAgeMax=extracted_ticket_data.get("child_age_max", 12),
            languages=extracted_ticket_data.get("languages") or ["EN"],
            timeTables=time_tables_list,
            duration=float(extracted_ticket_data.get("duration", 0) or 0),
            durationType=extracted_ticket_data.get("duration_type", "HOURS"),
        )
        ticket_option_payload = ticket_option.dict()
    except ValidationError as e:
        ticket_option_error = str(e)

    return {
        "supplier_id": pre_config.supplier_id,
        "main_ticket_code": f"TICKET-{pre_config.ticket_code}",  # our own guess, real code comes from the API response
        "main_ticket_payload": main_ticket_payload,
        "main_ticket_error": main_ticket_error,
        "ticket_option_payload": ticket_option_payload,
        "ticket_option_error": ticket_option_error,
        "geolocation_resolved": geoloc.get("valid", False),
        "geolocation_source": geoloc.get("source"),
        "geolocation_name": geoloc.get("name"),
        "geolocation_latitude": geoloc.get("latitude"),
        "geolocation_longitude": geoloc.get("longitude"),
        "is_indonesia": is_indonesia,
        "vesak_day_note": vesak_day_coverage_note() if is_indonesia else None,
        "effective_release_days": effective_release_days,
        "release_days_overridden": effective_release_days != pre_config.days_available_before_release,
        "has_real_pricing": any([
            extracted_ticket_data.get("base_adult_price", 0),
            extracted_ticket_data.get("base_children_price", 0),
            extracted_ticket_data.get("base_infant_price", 0),
        ]),
    }
