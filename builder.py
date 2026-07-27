from typing import Dict, Any, List
from pydantic import ValidationError
from schemas import HumanPreConfig, ContractClosedTourVO, build_datasheets, DatasheetEN, ItineraryItem, ContractClosedTourOptionVO, WEEKDAY_NAMES, SupplementVO, SupplementPriceVO, SupplementTranslation
from api_client import TravelCompositorAPI

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
        supplements_list.append(SupplementVO(
            translations={"EN": SupplementTranslation(name=s.get("name", ""))},
            price=SupplementPriceVO(singlePrice=price_val, doublePrice=price_val),
            mandatory=bool(s.get("mandatory", False)),
            onRequest=bool(s.get("on_request", False)),
            free=(price_val == 0),
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
            priceList=extracted_dmc_data.get("price_list", []),
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
