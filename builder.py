import math
from typing import Dict, Any, List
from pydantic import ValidationError
from schemas import HumanPreConfig, ContractClosedTourVO, build_datasheets, DatasheetEN, ItineraryItem, ContractClosedTourOptionVO, WEEKDAY_NAMES, SupplementVO, SupplementPriceVO, SupplementTranslation, OptionTranslation, CancellationRange
from schemas import TicketHumanPreConfig, ApiStaticContentTicketVO, ContractTicketModalityVO, GeolocationVO, MeetingPointVO, TicketDatasheetEN, TicketCancellationRange, TicketSupplementVO, TicketSupplementTranslation, TicketRemark
from schemas import TransferHumanPreConfig, ContractTransferVO, TransferLocationVO, TransferDescriptorVO, TransferAdditionalServiceVO, TransferAdditionalServiceTranslation, TransferMoneyVO, TransferOccupancyPriceVO, TransferSupplementVO, TransferPropertyVO, TransferPropertyTranslation
from api_client import TravelCompositorAPI
from geocoding_client import geocode

DEFAULT_MEETING_POINT = ("Meet your guide in the airport arrival hall or, if you are already in the "
                          "tour's starting city, in your hotel lobby.")


def _safe_float(value, fallback=0.0):
    """
    CONFIRMED FIX (real production crash, LXR-3): "Out of range float
    values are not JSON compliant: nan" - the `requests` library explicitly
    disallows NaN when serializing a `json=` payload (unlike Python's own
    json.dumps, which allows it by default), so any NaN float reaching a
    numeric payload field crashes at publish time with exactly this error.

    NaN commonly reaches here from a blank Streamlit data_editor cell: when
    a numeric column mixes a blank row with other rows holding real numbers,
    pandas silently promotes the blank cell to NaN (float) to keep the
    column's dtype consistent - the exact same promotion behavior already
    confirmed for text columns (see app.py's _safe_cell_str), just showing
    up in a numeric field this time. CRITICAL: NaN is TRUTHY in Python (only
    0/0.0/None/""/False are falsy), so the common "value or 0" guard does
    NOT catch it - float(nan or 0) still returns nan, not 0. This checks for
    NaN (and Infinity, equally invalid JSON) explicitly, on top of the
    normal None/non-numeric cases float() itself would raise on.
    """
    if value is None:
        return fallback
    try:
        result = float(value)
    except (TypeError, ValueError):
        return fallback
    if math.isnan(result) or math.isinf(result):
        return fallback
    return result


def _safe_int(value, fallback=0):
    """Same NaN/Infinity/non-numeric safety as _safe_float, but returns an int."""
    result = _safe_float(value, fallback=None)
    return fallback if result is None else int(result)


def _safe_supplement_price(value, fallback=0.0):
    """
    CONFIRMED FIX (real production crash, SUB-1): "float() argument must be
    a string or a real number, not 'dict'" - a supplement's price fields are
    supposed to be flat numbers, but AI extraction has occasionally produced
    a nested {"amount": ..., "currency": ...} object instead (the shape
    price_list rows use, and the two schemas sit right next to each other in
    the same prompt, so the AI confusing them is a real, observed failure
    mode) - or a merge/carry-forward step could copy one through unchanged.
    Rather than crashing the whole publish on one bad field, unwrap the
    common dict shape if present, then run it through _safe_float() (which
    also catches the separate NaN class of bug above) instead of ever
    calling float() on a raw, unchecked value.
    """
    if isinstance(value, dict):
        value = value.get("amount", fallback)
    return _safe_float(value, fallback)


def _cancellation_ranges_from_tiers(tiers):
    """
    Converts AI-extracted cancellation_policy_tiers (the SOURCE's own stated
    fee tiers, e.g. [{"days": 91, "fee_percentage": 25}, ...] - already
    sanitized upstream by ai_extractor.py's _sanitize_cancellation_tiers)
    into (days, refund_percentage) pairs matching Travel Compositor's
    CancellationRange/TicketCancellationRange shape.

    CONFIRMED (schemas.py's CancellationRange.percentage docstring, checked
    against real data): TC's "percentage" field is the REFUND percentage,
    the INVERSE of how suppliers normally state their policy ("25% fee" ->
    75% refund) - converted here via refund% = 100 - fee%.

    ASSUMPTION (not independently confirmed against a real multi-tier
    example - only the single flat 30-days/100%-refund case is confirmed):
    each entry means "cancel at least `days` days before arrival -> refund
    `percentage`%", i.e. Travel Compositor applies the entry with the
    largest `days` threshold that is <= the actual number of days before
    arrival at cancellation time. Sorted descending by days to match that
    reading - review this against a real multi-tier tour/ticket on Travel
    Compositor once one is live, and adjust here if the actual behavior
    turns out to be different.

    CONFIRMED REAL RULE (human feedback): this used to be hardcoded to a
    flat 30-days/100%-refund default regardless of what the source document
    actually said. Returns None (not an empty list) when `tiers` is falsy,
    so callers can tell "use the existing flat default" apart from "the
    source genuinely wants a 0-day/0%-refund policy".
    """
    if not tiers:
        return None
    cleaned = []
    for t in tiers:
        if not isinstance(t, dict):
            continue
        days = t.get("days")
        fee_pct = t.get("fee_percentage")
        if not isinstance(days, (int, float)) or not isinstance(fee_pct, (int, float)):
            continue
        refund_pct = max(0.0, min(100.0, 100.0 - _safe_float(fee_pct)))
        cleaned.append((int(days), refund_pct))
    if not cleaned:
        return None
    cleaned.sort(key=lambda pair: pair[0], reverse=True)
    return cleaned


def build_supplement_vos(supplements: List[Dict[str, Any]]) -> List[SupplementVO]:
    """
    Converts the app's internal flat supplement dicts (name/price/single_price/
    double_price/triple_price/quadruple_price/mandatory/on_request/applies_to/
    travel_start_date/travel_end_date) into the real SupplementVO wire shape.

    Factored out of build_closed_tour_payloads() so it can also be used
    standalone - e.g. when adding a brand-new Modality to an ALREADY-LIVE
    tour: that Modality's own supplements need to be folded into the tour's
    existing (already-live) supplements list via a follow-up PUT, entirely
    independent of building a full ContractClosedTourVO payload.
    """
    supplements_list = []
    for s in (supplements or []):
        price_val = _safe_supplement_price(s.get("price", 0))
        single_val = _safe_supplement_price(s.get("single_price", price_val), fallback=price_val)
        double_val = _safe_supplement_price(s.get("double_price", price_val), fallback=price_val)
        triple_val = _safe_supplement_price(s.get("triple_price", 0))
        quadruple_val = _safe_supplement_price(s.get("quadruple_price", 0))
        # NOTE: the confirmed schema's singlePrice/doublePrice/etc are inherently
        # per-person amounts (that's what "per occupancy" means in this API).
        # "Per Pax" unchecked is tracked for the human's own clarity, but we don't
        # have a confirmed API field for a genuinely flat/non-per-pax supplement
        # charge - if you need that, verify with Travel Compositor directly.
        travel_windows = []
        if s.get("travel_start_date") and s.get("travel_end_date"):
            travel_windows = [{"start": s["travel_start_date"], "end": s["travel_end_date"]}]

        # CONFIRMED FIX (triple-charging bug): scope this supplement to the
        # specific Modality it belongs to, instead of leaving modalityCodes
        # empty (which Travel Compositor treats as "applies to ALL Modalities"
        # on this tour) - previously EVERY supplement silently applied to
        # every Modality/room-category, stacking Standard + Superior +
        # Deluxe surcharges on top of each other on every single one of them.
        applies_to = str(s.get("applies_to") or "").strip()
        modality_codes = [] if applies_to in ("", "All Modalities", "ALL") else [applies_to]

        supplements_list.append(SupplementVO(
            translations={"EN": SupplementTranslation(name=s.get("name", ""))},
            price=SupplementPriceVO(singlePrice=single_val, doublePrice=double_val,
                                   triplePrice=triple_val, quadruplePrice=quadruple_val),
            modalityCodes=modality_codes,
            mandatory=bool(s.get("mandatory", False)),
            onRequest=bool(s.get("on_request", False)),
            free=(price_val == 0),
            travelWindows=travel_windows,
        ))
    return supplements_list


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


def normalize_time_hhmm(value: str) -> str:
    """
    CONFIRMED via a real API error (3 real tickets failed on this): unlike
    startTime/endTime above (which need HH:MM:SS), the Ticket Modality's
    timeTables field is deserialized server-side with a LocalTime format
    that ONLY accepts HH:MM - 'Value(HourOfDay,2)':'Value(MinuteOfHour,2)',
    no seconds component at all. Sending "08:00:00" fails with
    "Text '08:00:00' could not be parsed, unparsed text found at index 5"
    (index 5 is exactly where the trailing ":00" seconds starts). This
    strips any seconds instead, guaranteeing bare HH:MM regardless of what
    was extracted or typed in.
    """
    value = (value or "").strip()
    if not value:
        return ""
    parts = value.split(":")
    if len(parts) >= 2:
        return f"{parts[0]}:{parts[1]}"
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
                # ItineraryItem.destination is a required non-Optional str - an
                # unresolved destination returns tc_code=None, which used to
                # crash this whole function with an uncaught pydantic
                # ValidationError instead of reaching the friendly
                # "unresolved_destinations" warning UI already built for this
                # case (app.py, Step 6). "" is a safe placeholder: it can never
                # be a real Travel Compositor code, and can_publish already
                # requires unresolved_destinations to be empty before allowing
                # publish, so this can never silently reach the real API.
                destination=result["tc_code"] or "",
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

    effective_release_days = resolve_release_days(
        pre_config.days_available_before_release, extracted_dmc_data.get("release_days_mentions")
    )

    # This constant guess is used as the fallback tour code shown in the UI
    # BEFORE a real code is returned by the API, and (unlike main_tour.code)
    # stays available even if the main_tour construction below fails -
    # mirrors build_ticket_payloads' main_ticket_code pattern.
    main_tour_code_guess = extracted_dmc_data.get("tour_code") or f"TOUR-{pre_config.provider_code}"

    # 2. Build Main Tour Payload (ContractClosedTourVO)
    # NOTE: these fields are required `str`/`list`/`int` types on DatasheetEN /
    # ContractClosedTourVO. `.get(key, default)` only falls back to `default`
    # when the key is ABSENT - if the AI extraction explicitly returned
    # `None`, `.get()` still returns None and pydantic raises a
    # ValidationError. `or <fallback>` below guards against that for every
    # field, and the whole construction (including supplements and the
    # datasheet, both of which can also raise - e.g. float() on a non-numeric
    # supplement price) is wrapped in try/except below - this used to be
    # unguarded (unlike the Ticket path, which already had this exact
    # protection) and a single bad field crashed the entire build with an
    # uncaught ValidationError instead of degrading to a friendly per-field
    # error message like Tickets already did.
    main_tour_error = None
    main_tour_payload = None
    try:
        # Convert the simple flat supplement table into the confirmed real
        # SupplementVO structure (per-occupancy amounts are read straight from
        # the extracted/human-edited data - see build_supplement_vos()'s own
        # docstring/BASIS RULE reference for the math).
        supplements_list = build_supplement_vos(extracted_dmc_data.get("supplements", []))

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

        # CONFIRMED REAL RULE (human feedback): cancellation used to be
        # hardcoded to a flat 30-days/100%-refund default for every tour
        # regardless of what the supplier's own contract actually said -
        # that was wrong. Use the source's own extracted tiers (see
        # _cancellation_ranges_from_tiers's docstring for the fee->refund%
        # conversion and days-threshold assumption) whenever the source
        # stated a specific policy; otherwise keep the existing flat default
        # (CancellationRange()'s own 30-days/100% default) untouched.
        cancellation_tiers = _cancellation_ranges_from_tiers(extracted_dmc_data.get("cancellation_policy_tiers"))
        cancellation_ranges = (
            [CancellationRange(days=d, percentage=p) for d, p in cancellation_tiers]
            if cancellation_tiers else [CancellationRange()]
        )

        main_tour = ContractClosedTourVO(
            supplier=pre_config.supplier_code or pre_config.supplier_id,
            userId=pre_config.user_id,
            code=main_tour_code_guess,
            providerCode=pre_config.provider_code,
            name=extracted_dmc_data.get("tour_name") or "",
            datasheets=build_datasheets(datasheet_en),
            images=extracted_dmc_data.get("image_urls") or [],
            itinerary=validated_itinerary,
            transports=transports_count,
            hotels=hotels_count or 0,
            startTime=normalize_time_hhmmss(extracted_dmc_data.get("start_time", "")),
            endTime=normalize_time_hhmmss(extracted_dmc_data.get("end_time", "")),
            supplements=supplements_list,
            minChildAge=extracted_dmc_data.get("min_child_age") if extracted_dmc_data.get("min_child_age") is not None else pre_config.min_child_age,
            maxChildAge=extracted_dmc_data.get("max_child_age") if extracted_dmc_data.get("max_child_age") is not None else pre_config.max_child_age,
            currency=pre_config.currency,
            nights=extracted_dmc_data.get("nights") if extracted_dmc_data.get("nights") is not None else 1,
            minPax=pre_config.min_pax,
            maxPax=pre_config.max_pax,
            modalityCodes=[pre_config.modality_code],
            daysAvailableBeforeRelease=effective_release_days,
            cancellationRanges=cancellation_ranges,
            active=False  # LOCKED: Strictly upload as inactive/draft
        )
        main_tour_payload = main_tour.dict()
    except ValidationError as e:
        main_tour_error = str(e)
    except (ValueError, TypeError) as e:
        # Plain Python errors (e.g. a non-numeric string reaching a numeric
        # field before pydantic even sees it) used to propagate uncaught -
        # catch these too, not just ValidationError, same as the defensive
        # net just added to the Ticket path.
        main_tour_error = f"Couldn't build the tour payload - {e}"

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
    except (ValueError, TypeError) as e:
        tour_option_error = f"Couldn't build the tour option payload - {e}"

    return {
        "supplier_id": pre_config.supplier_id,
        "main_tour_code": main_tour_code_guess,
        "main_tour_payload": main_tour_payload,
        "main_tour_error": main_tour_error,
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
        # CONFIRMED ORDER (team decision): try Travel Compositor's own data
        # first - this supplier's transfer zones, if it has any configured -
        # before falling back to the free OpenStreetMap geocoder. TC's own
        # data is more reliable when it's actually there (no rate limits, no
        # cloud-IP blocking), but only covers suppliers that also do
        # transfers, so a miss here is normal and just means falling through
        # to the geocoder exactly as before.
        tz_result = api_client.resolve_transfer_zone_geolocation(pre_config.supplier_id, city)
        if tz_result.get("valid"):
            geoloc = {
                "latitude": tz_result["latitude"], "longitude": tz_result["longitude"],
                "name": tz_result.get("name") or city, "valid": True,
                "source": "Travel Compositor transfer zone (this supplier's own data)",
            }
        else:
            geo_result = geocode(city)
            # geocode() tries Nominatim first, then falls back to Photon if
            # Nominatim comes back empty (confirmed real issue: Nominatim often
            # returns zero results for cloud-hosted traffic like this app's,
            # even for well-known places) - report whichever provider actually
            # served this result rather than assuming it was always Nominatim.
            provider_labels = {"nominatim": "OpenStreetMap/Nominatim", "photon": "OpenStreetMap/Photon"}
            geoloc = {
                "latitude": geo_result["latitude"], "longitude": geo_result["longitude"],
                "name": geo_result.get("display_name") or city, "valid": geo_result["valid"],
                "source": provider_labels.get(geo_result.get("provider"), "OpenStreetMap") if geo_result["valid"] else "not_found",
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
            # Same TC-first, OpenStreetMap-fallback order as the main city
            # above - a named meeting point (a station, landmark, terminal)
            # is exactly the kind of thing that can show up as a transfer
            # zone's own POINT/AIRPORT/PORT entry for this supplier.
            mp_tz = api_client.resolve_transfer_zone_geolocation(pre_config.supplier_id, mp_desc)
            if mp_tz.get("valid"):
                lat, lng = mp_tz["latitude"], mp_tz["longitude"]
            else:
                mp_geo = geocode(f"{mp_desc}, {city}" if city else mp_desc)
                lat = mp_geo["latitude"] if mp_geo["valid"] else geoloc.get("latitude")
                lng = mp_geo["longitude"] if mp_geo["valid"] else geoloc.get("longitude")
        if lat is not None and lng is not None:
            meeting_points_out.append(MeetingPointVO(description=mp_desc, latitude=lat, longitude=lng))

    # Filter out any None/blank/literal-"None" garbage BEFORE normalizing -
    # confirmed via a real API error that a stray "None" string (from a blank
    # data_editor row upstream getting str()'d) reaches here and blows up
    # java.time.LocalTime deserialization server-side with a raw
    # DateTimeParseException. Also normalize to bare HH:MM (NOT HH:MM:SS -
    # confirmed via 3 real failed tickets that timeTables' LocalTime parser
    # rejects seconds entirely, see normalize_time_hhmm above).
    time_tables_list = [
        normalize_time_hhmm(t) for t in (extracted_ticket_data.get("time_tables", []) or [])
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

        # Convert supplements (ticket-specific shape: per-passenger-type + dates).
        # NOTE: `.get(key, "")` only applies "" when the key is ABSENT - a blank
        # data_editor row upstream can leave an explicit `None` here (the exact
        # class of bug already fixed for time_tables above), which used to crash
        # this construction OUTSIDE any try/except entirely, before this
        # function's own try block was even reached. `or ""` guards both cases.
        supplements_list = []
        for s in extracted_ticket_data.get("supplements", []):
            supplements_list.append(TicketSupplementVO(
                adultPriceSupplement=_safe_float(s.get("adult_price", 0)),
                childrenPriceSupplement=_safe_float(s.get("children_price", 0)),
                infantPriceSupplement=_safe_float(s.get("infant_price", 0)),
                startDate=s.get("travel_start_date") or "",
                endDate=s.get("travel_end_date") or "",
                translations={"EN": TicketSupplementTranslation(name=s.get("name", ""))},
            ))

        datasheet_en = TicketDatasheetEN(
            name=extracted_ticket_data.get("ticket_name") or "",
            description=extracted_ticket_data.get("description") or "",
            meetingPoint=extracted_ticket_data.get("meeting_point_summary") or "Hotel Lobby",
            departureTime=time_tables_list[0] if time_tables_list else "",
            voucherRemarks=extracted_ticket_data.get("voucher_remarks") or extracted_ticket_data.get("cancellation_policy_text") or "",
            includes=extracted_ticket_data.get("includes") or [],
            excludes=extracted_ticket_data.get("excludes") or [],
            activityType=extracted_ticket_data.get("activity_type"),
        )

        # CONFIRMED REAL RULE (human feedback): cancellation used to be
        # hardcoded to a flat 30-days/100%-refund default for every ticket
        # regardless of what the supplier's own contract actually said -
        # that was wrong. Use the source's own extracted tiers whenever the
        # source stated a specific policy (see _cancellation_ranges_from_tiers's
        # docstring for the fee->refund% conversion and days-threshold
        # assumption); otherwise keep the existing flat default untouched.
        ticket_cancellation_tiers = _cancellation_ranges_from_tiers(extracted_ticket_data.get("cancellation_policy_tiers"))
        ticket_cancellation_ranges = (
            [TicketCancellationRange(cancellationDays=d, cancellationPercentage=p) for d, p in ticket_cancellation_tiers]
            if ticket_cancellation_tiers else [TicketCancellationRange()]
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
            adultTaxesAmount=_safe_float(extracted_ticket_data.get("adult_taxes_amount", 0)),
            childTaxesAmount=_safe_float(extracted_ticket_data.get("child_taxes_amount", 0)),
            infantTaxesAmount=_safe_float(extracted_ticket_data.get("infant_taxes_amount", 0)),
            daysAvailableBeforeRelease=effective_release_days,
            duration=_safe_float(extracted_ticket_data.get("duration", 0)),
            durationType=extracted_ticket_data.get("duration_type", "HOURS"),
            cancellationRanges=ticket_cancellation_ranges,
            meetingPoints=meeting_points_out,
            active=False,  # LOCKED default - same confirmed workflow as ClosedTour applies
        )
        # NOTE: deliberately NOT reading extracted_ticket_data.get("product_types")
        # here anymore - product_types (Engines) always uses the curated safe
        # default list from ApiStaticContentTicketVO/schemas.py (confirmed
        # by the product owner). Letting AI-extracted data override that list
        # was a live footgun: a hallucinated/malformed value could silently
        # replace the known-good defaults with no validation against them.
        main_ticket = ApiStaticContentTicketVO(**main_ticket_kwargs)
        main_ticket_payload = main_ticket.dict()
    except ValidationError as e:
        main_ticket_error = str(e)
    except (ValueError, TypeError) as e:
        # Plain Python errors (e.g. a non-numeric string reaching float())
        # used to propagate uncaught instead of degrading to a friendly
        # per-field error message.
        main_ticket_error = f"Couldn't build the ticket payload - {e}"

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
        base_adult_price = _safe_float(extracted_ticket_data.get("base_adult_price", 0))
        base_children_price = _safe_float(extracted_ticket_data.get("base_children_price", 0))
        base_infant_price = _safe_float(extracted_ticket_data.get("base_infant_price", 0))
        base_service_price = _safe_float(extracted_ticket_data.get("base_service_price", 0))
        # Each row can carry the same NaN-from-a-blank-data_editor-cell risk
        # as any other numeric UI field (see _safe_float's docstring) - sanitize
        # every entry rather than trusting the list as passed through.
        occupancy_prices = [
            {"occupancy": _safe_int(o.get("occupancy", 1), fallback=1), "amount": _safe_float(o.get("amount", 0))}
            for o in (extracted_ticket_data.get("occupancy_prices") or []) if isinstance(o, dict)
        ]
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
            # CONFIRMED REAL REQUEST (human feedback): the "Condition" field
            # (Travel Compositor's per-modality remarks) used to always be
            # blank - now carries the same extracted cancellation policy text
            # shown on the Voucher Remarks field above, so staff see it too.
            remarks={"EN": TicketRemark(name=pre_config.modality_code, remarks=extracted_ticket_data.get("cancellation_policy_text") or None)},
            supplements=supplements_list,
            stopSales=combined_ticket_stop_sales,
            ticketsPerDay=99,
            disallowChildren=bool(extracted_ticket_data.get("disallow_children", False)),
            onRequest=pre_config.on_request,
            disallowInfant=bool(extracted_ticket_data.get("disallow_infant", False)),
            disallowAdult=bool(extracted_ticket_data.get("disallow_adult", False)),
            startDate=extracted_ticket_data.get("start_date") or "",
            endDate=extracted_ticket_data.get("end_date") or "",
            baseAdultPrice=base_adult_price,
            baseChildrenPrice=base_children_price,
            baseInfantPrice=base_infant_price,
            baseServicePrice=base_service_price,
            occupancyPrices=occupancy_prices,
            priceType=selected_price_type,
            maxPassengers=pre_config.max_passengers,
            minPassengers=pre_config.min_passengers,
            # Confirmed by product owner: infant = 0-2, child = 2-12,
            # internationally standard, same for Tickets and ClosedTours -
            # previously this defaulted to 6/12 here (unify with ClosedTour's
            # 2/12 default below, see HumanPreConfig in schemas.py).
            childAgeMin=extracted_ticket_data.get("child_age_min") if extracted_ticket_data.get("child_age_min") is not None else 2,
            childAgeMax=extracted_ticket_data.get("child_age_max") if extracted_ticket_data.get("child_age_max") is not None else 12,
            languages=extracted_ticket_data.get("languages") or ["EN"],
            timeTables=time_tables_list,
            duration=_safe_float(extracted_ticket_data.get("duration", 0)),
            durationType=extracted_ticket_data.get("duration_type", "HOURS"),
        )
        ticket_option_payload = ticket_option.dict()
    except ValidationError as e:
        ticket_option_error = str(e)
    except (ValueError, TypeError) as e:
        ticket_option_error = f"Couldn't build the ticket option payload - {e}"

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


# ==========================================
# TRANSFER PAYLOAD BUILDER
# Confirmed against 3 real supplier rate sheets and a series of
# product-owner clarifications - see schemas.py's Transfer section for the
# field-by-field real-data confirmations this maps onto.
# ==========================================

_TRANSFER_PRODUCT_TYPE_KEYWORDS = [
    ("LUXURY", ["luxury"]),
    ("PREMIUM", ["premium", "superior"]),
    ("SPECIAL", ["special"]),
    ("EXPRESS", ["express"]),
    ("STANDARD", ["standard"]),
    ("ECONOMY", ["economy", "budget"]),
]


def _map_transfer_product_type(class_hint: str) -> str:
    """Best-effort mapping of a supplier's free-text tier label to Travel Compositor's
    productType enum - UNCONFIRMED against real data beyond "Standard" (an exact match),
    reviewable/editable per record in the UI rather than blocking on a perfect mapping."""
    text = (class_hint or "").lower()
    for enum_val, keywords in _TRANSFER_PRODUCT_TYPE_KEYWORDS:
        if any(k in text for k in keywords):
            return enum_val
    return "ECONOMY"


_TRANSFER_SERVICE_TYPE_KEYWORDS = [
    ("SHARED", ["seat in coach", "seat-in-coach", "joint", "shared"]),
    ("SHUTTLE", ["shuttle"]),
    ("PRIVATE", ["private", "exclusive"]),
]


def _map_transfer_service_type(service_name: str) -> str:
    """CONFIRMED distinction from real data: a supplier's 'ChargeUnit-Pax' shared/seat-in-coach
    service maps to SHARED/SHUTTLE, while a 'ChargeUnit-Service' flat-per-vehicle service maps to
    PRIVATE - every real live example seen so far was PRIVATE. Order matters: check the more
    specific "seat in coach" phrasing before the generic "shuttle" keyword."""
    text = (service_name or "").lower()
    for enum_val, keywords in _TRANSFER_SERVICE_TYPE_KEYWORDS:
        if any(k in text for k in keywords):
            return enum_val
    return "PRIVATE"


_TRANSFER_VEHICLE_TYPE_KEYWORDS = [
    ("MINIVAN", ["mini-van", "minivan", "mini van", "van"]),
    ("COACH", ["coach", "bus", "micro bus", "minibus"]),
    ("LIMOUSINE", ["limo", "limousine"]),
    ("CAR", ["car", "sedan", "avanza", "innova", "premio"]),
]


def _map_transfer_vehicle_type(vehicle_hint: str, service_name: str) -> str:
    """Best-effort mapping - UNCONFIRMED against the full ~30-value vehicleType enum (only
    the exact text "CAR" is confirmed via real live data), reviewable/editable per record."""
    text = f"{vehicle_hint or ''} {service_name or ''}".lower()
    for enum_val, keywords in _TRANSFER_VEHICLE_TYPE_KEYWORDS:
        if any(k in text for k in keywords):
            return enum_val
    return "CAR"


# CONFIRMED REAL SYSTEM LIMIT (product owner): Travel Compositor caps a single Transfer
# booking at 9 passengers / 4 rooms regardless of what a supplier's rate sheet prices above
# that - any occupancy tier above this is genuinely unbookable in TC, so it's dropped rather
# than sent (an occupancy of 10+ would likely be rejected by the API anyway, and there is no
# reason to carry pricing data TC can never actually use).
_MAX_TRANSFER_OCCUPANCY = 9

# CONFIRMED REAL RULE (product owner, ~99% of real contracts): almost every transfer is
# door-to-door regardless of service type/tier - NOT conditional on Private vs Shared as
# originally guessed. Applied as the default property for every transfer; removable per
# record in the review UI for the rare exception.
_DEFAULT_TRANSFER_PROPERTIES = [
    TransferPropertyVO(propertyType="DOORTODOOR", translations={"EN": TransferPropertyTranslation(description="Door to Door")}),
]


def build_transfer_payload(
    pre_config: TransferHumanPreConfig,
    extracted_transfer_data: Dict[str, Any],
    api_client: TravelCompositorAPI,
    existing_transfer_id: str = None,
    existing_transfer_snapshot: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """
    Builds one ContractTransferVO payload from AI-extracted rate-sheet data
    (see ai_extractor.py's extract_transfer_data). Unlike ClosedTour/Ticket,
    there's no human-assigned code to key this off of - `existing_transfer_id`
    (from a confirmed match, see transfer_matcher.py) gets set on the
    payload's own 'id' field when this is an update, and left None for a
    fresh create; api_client.create_transfer/update_transfer decide which
    endpoint to call based on which flow the human is in, not on this value.

    `existing_transfer_snapshot`: the full current GET /transfer/{supplierId}/{id}
    response, if this is an update (the caller fetches it - see app.py). CONFIRMED
    REAL RULE (product owner): a live transfer is typically already bookable far
    into the future (real examples show endDate=2049-12-31) - a seasonal rate-sheet
    refresh should update PRICING, not narrow that validity window back down to
    whatever season text happens to be printed on this year's sheet. When a
    snapshot is given, startDate/endDate/images/properties are preserved from the
    EXISTING live record rather than overwritten by the newly extracted document -
    this is the "merge, don't overwrite" behavior promised for updates. For a
    fresh create (no snapshot), the extracted document's own season dates and
    default door-to-door property are used instead, as before.
    """
    departure_name = extracted_transfer_data.get("departure_name", "") or ""
    arrival_name = extracted_transfer_data.get("arrival_name", "") or ""
    is_zone_based = bool(extracted_transfer_data.get("is_zone_based", False))

    def _resolve_location(place_name):
        """CONFIRMED ORDER: for zone-based (area) routing, resolve against this supplier's
        own Transfer Zones first, using resolve_transfer_zone() (returns a real zone id for
        departureLocationId/arrivalLocationId - appropriate for a broad named area rather
        than one GPS pin, e.g. Bali's "South Bali (Tuban/Kuta/...)"). For point-to-point
        routing, or when no matching zone exists for this supplier, fall back to raw
        geolocation - transfer-zone coordinates first, then the free OpenStreetMap geocoder -
        same confirmed TC-first order used everywhere else in this app."""
        if is_zone_based:
            zr = api_client.resolve_transfer_zone(pre_config.supplier_id, place_name)
            if zr.get("valid"):
                return {
                    "name": zr.get("name") or place_name, "latitude": zr.get("latitude"),
                    "longitude": zr.get("longitude"), "zone_radius": zr.get("zone_radius"),
                    "zone_id": zr.get("zone_id"), "valid": True, "source": "transfer_zone",
                }
        tz_result = api_client.resolve_transfer_zone_geolocation(pre_config.supplier_id, place_name)
        if tz_result.get("valid"):
            return {
                "name": tz_result.get("name") or place_name, "latitude": tz_result["latitude"],
                "longitude": tz_result["longitude"], "zone_radius": None, "zone_id": None,
                "valid": True, "source": "transfer_zone",
            }
        geo_result = geocode(place_name)
        provider_labels = {"nominatim": "OpenStreetMap/Nominatim", "photon": "OpenStreetMap/Photon"}
        return {
            "name": geo_result.get("display_name") or place_name,
            "latitude": geo_result.get("latitude"), "longitude": geo_result.get("longitude"),
            "zone_radius": None, "zone_id": None,
            "valid": geo_result.get("valid", False),
            "source": provider_labels.get(geo_result.get("provider"), "OpenStreetMap") if geo_result.get("valid") else "not_found",
        }

    departure_geo = _resolve_location(departure_name)
    arrival_geo = _resolve_location(arrival_name)

    payload_error = None
    payload = None
    try:
        departure_loc = TransferLocationVO(
            name=departure_geo.get("name") or departure_name,
            geolocation=(GeolocationVO(latitude=departure_geo["latitude"], longitude=departure_geo["longitude"])
                         if departure_geo.get("latitude") is not None and departure_geo.get("longitude") is not None else None),
            zoneRadius=departure_geo.get("zone_radius"),
        )
        arrival_loc = TransferLocationVO(
            name=arrival_geo.get("name") or arrival_name,
            geolocation=(GeolocationVO(latitude=arrival_geo["latitude"], longitude=arrival_geo["longitude"])
                         if arrival_geo.get("latitude") is not None and arrival_geo.get("longitude") is not None else None),
            zoneRadius=arrival_geo.get("zone_radius"),
        )

        service_name = extracted_transfer_data.get("service_name") or "Transfer"
        class_hint = extracted_transfer_data.get("class_or_product_type") or ""
        name_prefix = service_name if not class_hint or class_hint.lower() in service_name.lower() \
            else f"{service_name} ({class_hint})"
        transfer_name = f"{name_prefix}: {departure_name} - {arrival_name}".strip(": ")

        # CONFIRMED FALLBACK RULE (product owner decision): when the document states no
        # specific cancellation terms, fall back to the same 30-day/100%-refund default
        # used everywhere else in this app - expressed as text here since Transfer has no
        # structured cancellation field, unlike ClosedTour/Ticket.
        cancellation_tiers = _cancellation_ranges_from_tiers(extracted_transfer_data.get("cancellation_policy_tiers"))
        if cancellation_tiers:
            voucher_text = extracted_transfer_data.get("cancellation_policy_text") or ""
        else:
            voucher_text = ("Free cancellation up to 30 days before arrival. Cancellation fees apply "
                             "within 30 days of arrival or for no-shows.")
        # CONFIRMED RULE (product owner): a location-conditional cost that can't be safely
        # auto-applied to price (e.g. a harbor-only pickup fee on a route that also serves
        # airport pickups) becomes an informational voucher note instead - never a mandatory
        # charge applied to every booking on the route.
        location_note = extracted_transfer_data.get("location_notes") or ""
        if location_note:
            voucher_text = f"{voucher_text}\n\n{location_note}" if voucher_text else location_note

        datasheet_en = TransferDescriptorVO(
            name=transfer_name,
            description=extracted_transfer_data.get("description") or "",
            pickupDescription=extracted_transfer_data.get("pickup_information") or "",
            voucherRemarks=voucher_text,
        )

        charge_unit = (extracted_transfer_data.get("charge_unit") or "per_pax").lower()
        price_by_pax = charge_unit != "per_service"
        currency = extracted_transfer_data.get("currency") or pre_config.currency

        # CONFIRMED REAL SYSTEM LIMIT (product owner): TC caps bookings at 9 passengers - a
        # supplier rate sheet pricing larger vehicles (e.g. a 9-14 pax coach tier) is pricing
        # something TC can never actually book, so those tiers are dropped here rather than
        # sent, and never counted toward max_occupancy below.
        tiers = [
            t for t in (extracted_transfer_data.get("occupancy_price_tiers") or [])
            if isinstance(t, dict) and _safe_int(t.get("occupancy", 1), fallback=1) <= _MAX_TRANSFER_OCCUPANCY
        ]
        tiers_sorted = sorted(tiers, key=lambda t: _safe_int(t.get("occupancy", 1), fallback=1))

        # CONFIRMED SEMANTICS (product owner): basePrice is the DEFAULT per-occupancy rate;
        # pricesByOccupancy only needs entries for occupancies that DIFFER from it (real
        # example: basePrice=11 covering occupancy 2-4, with only occupancy=1 listed at
        # double that as a solo-traveler surcharge). When a document instead gives a fully
        # explicit rate per bracket (e.g. Bali's 1/2/3-5/6-8/9-14 tiers), we don't try to
        # guess which single tier TC would treat as "the" implicit default - safer to list
        # every stated tier explicitly here, and use the smallest occupancy's rate as the
        # top-level basePrice (a visible, editable default).
        base_price = _safe_float(tiers_sorted[0].get("price", 0)) if tiers_sorted else 0.0
        min_occupancy = _safe_int(extracted_transfer_data.get("min_occupancy", 1), fallback=1) or 1
        max_occupancy = min(
            _safe_int(extracted_transfer_data.get("max_occupancy", 4), fallback=4) or 1,
            _MAX_TRANSFER_OCCUPANCY,
        )

        def _money(amount):
            return TransferMoneyVO(amount=_safe_float(amount), currency=currency)

        prices_by_occupancy = []
        for t in tiers_sorted:
            occ = _safe_int(t.get("occupancy", 1), fallback=1)
            child_price = t.get("child_price")
            infant_price = t.get("infant_price")
            prices_by_occupancy.append(TransferOccupancyPriceVO(
                occupancy=occ,
                basePrice=_money(t.get("price", 0)),
                childPrice=_money(child_price) if child_price is not None else TransferMoneyVO(currency=currency),
                infantPrice=_money(infant_price) if infant_price is not None else TransferMoneyVO(currency=currency),
                priceByPax=price_by_pax,
            ))

        # OPTIONAL/on-request extras - confirmed rule: child seats, non-default guide
        # languages, and similar all belong here (never in supplements, which is mandatory-
        # only). An "on request" qualifier gets folded into the name text itself since this
        # schema has no structured on-request flag.
        additional_services = []
        for a in (extracted_transfer_data.get("additional_services") or []):
            if not isinstance(a, dict):
                continue
            svc_name = a.get("name") or ""
            if a.get("on_request") and "request" not in svc_name.lower():
                svc_name = f"{svc_name} (on request)".strip()
            additional_services.append(TransferAdditionalServiceVO(
                currency=a.get("currency") or currency,
                maximum=_safe_int(a.get("max_quantity", 1), fallback=1) or 1,
                price=_safe_float(a.get("price", 0)),
                translations={"EN": TransferAdditionalServiceTranslation(name=svc_name)},
            ))
        # CONFIRMED RULE: guide language is never included by default (driver-only is the
        # base) - each other language priced in the source becomes its own optional
        # additionalServices surcharge rather than a separate whole transfer record.
        for g in (extracted_transfer_data.get("guide_language_surcharges") or []):
            if not isinstance(g, dict):
                continue
            language = g.get("language") or ""
            if not language:
                continue
            additional_services.append(TransferAdditionalServiceVO(
                currency=currency,
                maximum=max_occupancy,
                price=_safe_float(g.get("surcharge_estimate", 0)),
                translations={"EN": TransferAdditionalServiceTranslation(name=f"{language}-speaking guide")},
            ))

        # MANDATORY, unconditional surcharges only (confirmed rule) - see location_notes
        # handling above for why a location-conditional fee never ends up here.
        supplements = [
            TransferSupplementVO(name=s.get("name") or "", amount=_safe_float(s.get("amount", 0)))
            for s in (extracted_transfer_data.get("mandatory_supplements") or []) if isinstance(s, dict)
        ]

        # CONFIRMED REAL RULE (product owner decision, refined after real usage): a fresh
        # CREATE writes the document's own stated season dates (different documents can
        # genuinely differ). An UPDATE to an already-live transfer instead PRESERVES that
        # transfer's existing startDate/endDate/images/properties - a live transfer is
        # typically already bookable far into the future, and a seasonal rate refresh should
        # update pricing, not narrow that window back down to this year's printed season text.
        if existing_transfer_snapshot:
            effective_start_date = existing_transfer_snapshot.get("startDate") or extracted_transfer_data.get("start_date") or ""
            effective_end_date = existing_transfer_snapshot.get("endDate") or extracted_transfer_data.get("end_date") or ""
            effective_images = existing_transfer_snapshot.get("images") or []
            effective_properties = existing_transfer_snapshot.get("properties") or [p.dict() for p in _DEFAULT_TRANSFER_PROPERTIES]
        else:
            effective_start_date = extracted_transfer_data.get("start_date") or ""
            effective_end_date = extracted_transfer_data.get("end_date") or ""
            effective_images = []
            effective_properties = [p.dict() for p in _DEFAULT_TRANSFER_PROPERTIES]

        transfer_kwargs = dict(
            active=True,
            id=existing_transfer_id,
            name=transfer_name,
            productType=_map_transfer_product_type(class_hint),
            serviceType=_map_transfer_service_type(service_name),
            vehicleType=_map_transfer_vehicle_type(extracted_transfer_data.get("vehicle_hint"), service_name),
            departure=departure_loc,
            arrival=arrival_loc,
            departureLocationId=departure_geo.get("zone_id"),
            arrivalLocationId=arrival_geo.get("zone_id"),
            pickupInformation=extracted_transfer_data.get("pickup_information") or None,
            datasheets={"EN": datasheet_en},
            images=effective_images,
            properties=effective_properties,
            startDate=effective_start_date,
            endDate=effective_end_date,
            releaseContract=pre_config.days_available_before_release,
            currency=currency,
            basePrice=base_price,
            maxOccupancy=max_occupancy,
            minOccupancy=min_occupancy,
            # Decoupled from max_occupancy (a prior version conflated "how many passengers"
            # with "how many separate vehicles" - unrelated concepts that only coincidentally
            # matched in the one real example seen). 4 matches that confirmed real example;
            # allowMultipleVehicles is what actually lets larger groups span >1 vehicle.
            maxVehicles=4,
            allowMultipleVehicles=True,
            pricesByOccupancy=prices_by_occupancy,
            priceByPax=price_by_pax,
            supplements=supplements,
            stopSales=[],
            additionalServices=additional_services,
        )
        transfer = ContractTransferVO(**transfer_kwargs)
        payload = transfer.dict()
    except ValidationError as e:
        payload_error = str(e)
    except (ValueError, TypeError) as e:
        payload_error = f"Couldn't build the transfer payload - {e}"

    return {
        "supplier_id": pre_config.supplier_id,
        "transfer_payload": payload,
        "transfer_error": payload_error,
        "transfer_name": extracted_transfer_data.get("service_name") or "",
        "departure_name": departure_name,
        "arrival_name": arrival_name,
        "departure_geolocation_resolved": departure_geo.get("valid", False),
        "departure_geolocation_source": departure_geo.get("source"),
        "arrival_geolocation_resolved": arrival_geo.get("valid", False),
        "arrival_geolocation_source": arrival_geo.get("source"),
        "is_zone_based": is_zone_based,
        "existing_transfer_id": existing_transfer_id,
    }
