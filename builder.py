from typing import Dict, Any, List
from pydantic import ValidationError
from schemas import HumanPreConfig, ContractClosedTourVO, build_datasheets, DatasheetEN, ItineraryItem, ContractClosedTourOptionVO, WEEKDAY_NAMES
from api_client import TravelCompositorAPI

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
    raw_locations = extracted_dmc_data.get("itinerary_destinations", [])

    for loc_name in raw_locations:
        result = api_client.resolve_destination(loc_name)
        if not result["valid"]:
            # Flag it instead of silently uploading a made-up code.
            unresolved_destinations.append(loc_name)
        validated_itinerary.append(
            ItineraryItem(
                description={},
                destination=result["tc_code"],
                hotelsId=[]
            )
        )

    # 2. Build Main Tour Payload (ContractClosedTourVO)
    datasheet_en = DatasheetEN(
        name=extracted_dmc_data.get("tour_name", ""),
        description=extracted_dmc_data.get("description", ""),
        hotels=extracted_dmc_data.get("hotels_text", ""),
        voucherRemarks="",
        included=extracted_dmc_data.get("included", ""),
        excluded=extracted_dmc_data.get("excluded", ""),
        meetingPoint=extracted_dmc_data.get("meeting_point", ""),
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
        minChildAge=pre_config.min_child_age,
        maxChildAge=pre_config.max_child_age,
        currency=pre_config.currency,
        nights=extracted_dmc_data.get("nights", 1),
        minPax=pre_config.min_pax,
        maxPax=pre_config.max_pax,
        modalityCodes=[pre_config.modality_code],
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
            stopSales=[],
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
        "unresolved_destinations": unresolved_destinations  # surface these in the Review UI before publishing
    }
