from typing import Dict, Any, List
from pydantic import ValidationError
from schemas import HumanPreConfig, ContractClosedTourVO, build_datasheets, DatasheetEN, ItineraryItem, ContractClosedTourOptionVO, WEEKDAY_NAMES, SupplementVO, SupplementPriceVO, SupplementTranslation, OptionTranslation
from schemas import TicketHumanPreConfig, ApiStaticContentTicketVO, ContractTicketModalityVO, GeolocationVO, MeetingPointVO, TicketDatasheetEN, TicketCancellationRange, TicketSupplementVO, TicketSupplementTranslation, TicketRemark
from api_client import TravelCompositorAPI
from geocoding_client import geocode

DEFAULT_MEETING_POINT = ("Meet your guide in the airport arrival hall or, if you are already in the "
                          "tour's starting city, in your hotel lobby.")

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
    datasheet_en = DatasheetEN(
        name=extracted_dmc_data.get("tour_name", ""),
        description=extracted_dmc_data.get("description", ""),
        hotels=extracted_dmc_data.get("hotels_text", ""),
        voucherRemarks="",
        included=extracted_dmc_data.get("included", ""),
        excluded=extracted_dmc_data.get("excluded", ""),
        meetingPoint=extracted_dmc_data.get("meeting_point") or DEFAULT_MEETING_POINT,
        remarksTitle="Policy",
        remarksDescription=extracted_dmc_data.get("policy_remarks", "")
    )

    main_tour = ContractClosedTourVO(
        supplier=pre_config.supplier_code or pre_config.supplier_id,
        userId=pre_config.user_id,
        code=extracted_dmc_data.get("tour_code", f"TOUR-{pre_config.provider_code}"),
        providerCode=pre_config.provider_code,
        name=extracted_dmc_data.get("tour_name", ""),
        datasheets=build_datasheets(datasheet_en),
        images=extracted_dmc_data.get("image_urls", []),
        itinerary=validated_itinerary,
        transports=transports_count,
        hotels=hotels_count,
        startTime=extracted_dmc_data.get("start_time", ""),
        endTime=extracted_dmc_data.get("end_time", ""),
        supplements=supplements_list,
        minChildAge=pre_config.min_child_age,
        maxChildAge=pre_config.max_child_age,
        currency=pre_config.currency,
        nights=extracted_dmc_data.get("nights", 1),
        minPax=pre_config.min_pax,
        maxPax=pre_config.max_pax,
        modalityCodes=[pre_config.modality_code],
        daysAvailableBeforeRelease=pre_config.days_available_before_release,
        active=False  # LOCKED: Strictly upload as inactive/draft
    )

    # 3. Build Closed Tour Option Payload (ContractClosedTourOptionVO)
    # NOTE: priceList is required by the API, but we don't want to hard-crash
    # here during a preview/dry-run before pricing has been entered. Catch
    # the validation error and surface it as data instead; the actual
    # publish step (in web_extractor.py) still refuses to upload if this
    # error is present.
    tour_option_payload = None
    tour_option_error = None
    try:
        tour_option = ContractClosedTourOptionVO(
            code=pre_config.modality_code,
            operationalDays=extracted_dmc_data.get("operational_days", WEEKDAY_NAMES.copy()),
            stopSales=extracted_dmc_data.get("stop_sales", []),
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
        "itinerary_resolution": itinerary_resolution  # per-item status for clean green/red UI display
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

    datasheet_en = TicketDatasheetEN(
        name=extracted_ticket_data.get("ticket_name", ""),
        description=extracted_ticket_data.get("description", ""),
        meetingPoint=extracted_ticket_data.get("meeting_point_summary") or "Hotel Lobby",
        includes=extracted_ticket_data.get("includes", []),
        excludes=extracted_ticket_data.get("excludes", []),
        activityType=extracted_ticket_data.get("activity_type"),
    )

    main_ticket_error = None
    main_ticket_payload = None
    try:
        main_ticket = ApiStaticContentTicketVO(
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
            daysAvailableBeforeRelease=pre_config.days_available_before_release,
            duration=float(extracted_ticket_data.get("duration", 0) or 0),
            durationType=extracted_ticket_data.get("duration_type", "HOURS"),
            cancellationRanges=[TicketCancellationRange()],  # LOCKED default: always 30 days / 100%, matching ClosedTour's confirmed convention
            meetingPoints=meeting_points_out,
            active=False,  # LOCKED default - same confirmed workflow as ClosedTour applies
        )
        main_ticket_payload = main_ticket.dict()
    except ValidationError as e:
        main_ticket_error = str(e)

    ticket_option_payload = None
    ticket_option_error = None
    try:
        ticket_option = ContractTicketModalityVO(
            code=pre_config.modality_code,
            operationalDays=extracted_ticket_data.get("operational_days", WEEKDAY_NAMES.copy()),
            remarks={"EN": TicketRemark(name=pre_config.modality_code, remarks=None)},
            supplements=supplements_list,
            stopSales=extracted_ticket_data.get("stop_sales", []),
            ticketsPerDay=99,
            disallowChildren=bool(extracted_ticket_data.get("disallow_children", False)),
            onRequest=pre_config.on_request,
            disallowInfant=bool(extracted_ticket_data.get("disallow_infant", False)),
            disallowAdult=bool(extracted_ticket_data.get("disallow_adult", False)),
            startDate=extracted_ticket_data.get("start_date", ""),
            endDate=extracted_ticket_data.get("end_date", ""),
            baseAdultPrice=float(extracted_ticket_data.get("base_adult_price", 0) or 0),
            baseChildrenPrice=float(extracted_ticket_data.get("base_children_price", 0) or 0),
            baseInfantPrice=float(extracted_ticket_data.get("base_infant_price", 0) or 0),
            maxPassengers=pre_config.max_passengers,
            minPassengers=pre_config.min_passengers,
            childAgeMin=extracted_ticket_data.get("child_age_min", 6),
            childAgeMax=extracted_ticket_data.get("child_age_max", 12),
            languages=extracted_ticket_data.get("languages") or ["EN"],
            timeTables=extracted_ticket_data.get("time_tables", []),
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
        "has_real_pricing": any([
            extracted_ticket_data.get("base_adult_price", 0),
            extracted_ticket_data.get("base_children_price", 0),
            extracted_ticket_data.get("base_infant_price", 0),
        ]),
    }
