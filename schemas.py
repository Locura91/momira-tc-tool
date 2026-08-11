
# Stamped on every delivery. app.py compares this against its own build string and says
# so on screen when they differ - a partial push (one file committed, another not) used to
# surface only as a traceback whose line numbers pointed at unrelated code.
MODULE_BUILD = "2026-08-11-child-age"

from typing import List, Optional, Dict
from pydantic import BaseModel, Field, validator
import re

# ==========================================
# 1. HUMAN PRE-CONFIGURATION SCHEMA
# ==========================================
class HumanPreConfig(BaseModel):
    supplier_id: str = Field(..., example="48940", description="Numeric Travel Compositor Supplier ID, used in the URL path")
    supplier_code: Optional[str] = Field(
        None, example="Momira_CN_SC",
        description="Value for the JSON body's 'supplier' field, if it differs from the numeric Supplier ID "
                    "(confirmed these can be different values, e.g. a real tour showed supplier_id in the URL "
                    "but 'Momira_CN_SC' as the body's supplier field). Defaults to Supplier ID if left blank."
    )
    provider_code: str = Field(..., example="ASW-1", description="Format: XXX-Number")
    min_pax: int = Field(..., description="Must be 1 or 2")
    max_pax: int = Field(..., description="Must be between 2 and 9")
    currency: str = Field(..., example="EUR", description="ISO 3-letter currency code")
    modality_code: str = Field(..., example="STANDARD_CABIN", description="Modality / Option Code")
    on_request: bool = Field(True, description="True for On Request, False for Instant Confirmation")
    days_available_before_release: int = Field(30, description="How many days before departure this tour becomes bookable/visible")
    
    # System Hardcoded Defaults
    user_id: str = "Christian"
    # Confirmed by product owner: internationally standard age bands -
    # infant = 0-2, child = 2-12 - same convention for ClosedTours and
    # Tickets (see ContractTicketModalityVO's childAgeMin/Max in builder.py).
    min_child_age: int = 2
    max_child_age: int = 12

    @validator("provider_code")
    def validate_provider_code(cls, v):
        if not re.match(r"^[A-Z]{3}-\d+$", v):
            raise ValueError("providerCode must strictly follow the format 'XXX-Number' (e.g., ASW-1)")
        return v

    @validator("min_pax")
    def validate_min_pax(cls, v):
        if v not in [1, 2]:
            raise ValueError("minPax must be either 1 or 2")
        return v

    @validator("max_pax")
    def validate_max_pax(cls, v):
        if v not in range(2, 10):
            raise ValueError("maxPax must be between 2 and 9")
        return v


# ==========================================
# 2. TRAVEL COMPOSITOR MAIN TOUR SCHEMA
# ==========================================
class DatasheetEN(BaseModel):
    name: str
    description: str
    hotels: str = ""
    voucherRemarks: str = ""
    included: str
    excluded: str
    meetingPoint: str = ""
    remarksTitle: str = "Policy"
    remarksDescription: str = ""

def build_datasheets(english: DatasheetEN, extra: Optional[Dict[str, DatasheetEN]] = None) -> Dict[str, DatasheetEN]:
    """
    Travel Compositor stores 'datasheets' as a dynamic map keyed by
    UPPERCASE language code, e.g. {"EN": {...}, "ES": {...}} - not a fixed
    'en' field. This builds that structure; English is required, other
    languages can be added later via `extra`.
    """
    result = {"EN": english}
    if extra:
        result.update(extra)
    return result

class CancellationRange(BaseModel):
    days: int = 30
    percentage: float = 100.0  # confirmed against real data: this is REFUND %, so 100 = fully refundable 30+ days prior

class ItineraryItem(BaseModel):
    code: Optional[str] = None       # per-stop code (confirmed present in real schema)
    destination: str
    nights: Optional[int] = None     # nights spent at this specific stop
    description: dict = {}           # language-keyed, e.g. {"EN": "..."}
    image: Optional[str] = None
    hotels: Optional[str] = None     # free-text hotel description for this stop
    hotelsId: List[str] = []

class MoneyVO(BaseModel):
    amount: float
    currency: str

class ContractClosedTourPriceVO(BaseModel):
    """
    DEPRECATED per Travel Compositor's own docs: "price field is deprecated
    and will be ignored. Prices are now loaded directly into each closed
    tour option." Kept here only because real GET responses still show it
    (legacy read compatibility) - do not bother populating this on write,
    it has no effect. Real pricing lives in ContractClosedTourOptionVO.priceList.
    """
    singlePrice: Optional[MoneyVO] = None
    doublePrice: Optional[MoneyVO] = None
    triplePrice: Optional[MoneyVO] = None
    quadruplePrice: Optional[MoneyVO] = None
    tripleChildPercentageDiscount: Optional[float] = None
    quadrupleChildPercentageDiscount: Optional[float] = None

class ChildDiscount(BaseModel):
    amount: float = 0.0
    percentage: bool = True

class SupplementPriceVO(BaseModel):
    singlePrice: float = 0.0
    singleChildDiscount: Optional[ChildDiscount] = None
    doublePrice: float = 0.0
    doubleChildDiscount: Optional[ChildDiscount] = None
    triplePrice: float = 0.0
    tripleChildDiscount: Optional[ChildDiscount] = None
    quadruplePrice: float = 0.0
    quadrupleChildDiscount: Optional[ChildDiscount] = None

class SupplementTranslation(BaseModel):
    name: str

class SupplementVO(BaseModel):
    """
    Confirmed against a real GET /closedtour/{supplierId}/{code} response
    (supplier 449015, PEK-1) - e.g. optional excursions/meals like
    'Day 3: Dinner at Haidilao Restaurant'.
    """
    modalityCodes: List[str] = []
    translations: Dict[str, SupplementTranslation] = {}
    price: Optional[SupplementPriceVO] = None
    occupancyPrices: List[dict] = []
    occupancyDiscounts: List[dict] = []
    travelWindows: List[dict] = []
    bookingWindows: List[dict] = []
    mandatory: bool = False
    commissionable: bool = True
    refundable: bool = True
    priceInPercentage: bool = False
    free: bool = False
    onRequest: bool = False

class ContractClosedTourVO(BaseModel):
    supplier: str
    userId: str = "Christian"
    code: str
    providerCode: str
    name: str
    datasheets: Dict[str, DatasheetEN]
    images: List[str] = []
    itinerary: List[ItineraryItem] = []
    startTime: str = ""
    endTime: str = ""
    minChildAge: int = 2  # infant = 0-2, child = 2-12 (confirmed international convention)
    maxChildAge: int = 12
    hotels: int = 1
    transports: int = 0
    currency: str
    showHotelsFromDataSheet: bool = True
    showItineraryDescription: bool = False
    price: Optional[ContractClosedTourPriceVO] = None  # optional, no asterisk in real schema
    nights: int
    minPax: int
    maxPax: int
    modalityCodes: List[str] = []
    daysAvailableBeforeRelease: int = 0
    cancellationRanges: List[CancellationRange] = [CancellationRange()]
    active: bool = False  # Default/final state is inactive (draft). CONFIRMED: a tour must
                          # be temporarily active:true to be visible for creating/updating its
                          # options - see the create-new-tour flow in app.py, which creates
                          # active, adds the option, then switches back to inactive.
    downloadMode: str = "AUTOMATIC"
    supplements: List[SupplementVO] = []


# ==========================================
# 3. TRAVEL COMPOSITOR CLOSED TOUR OPTION SCHEMA (Call 2)
# ==========================================
# POST /closedtour/{supplierId}/{closedTourCode}
#
# NOTE: The exact structure of individual `priceList` entries and
# `translations` values wasn't provided yet. Modeled loosely as dicts for
# now so validation doesn't break on real data - tighten these once we
# have an example priceList item from Travel Compositor's docs/support.
WEEKDAY_NAMES = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]

class StopSale(BaseModel):
    start: str  # ISO date "YYYY-MM-DD"
    end: str

class QuantityPerDate(BaseModel):
    date: str
    manualSold: int = 0
    initialCapacity: int = 0
    onRequestManualSold: int = 0
    onRequestInitialCapacity: int = 0

class OptionTranslation(BaseModel):
    name: Optional[str] = None
    remarks: Optional[str] = None

class PriceListPriceVO(BaseModel):
    """Same per-occupancy shape as the main tour's (deprecated) price block, but THIS one is live/used."""
    singlePrice: Optional[MoneyVO] = None
    doublePrice: Optional[MoneyVO] = None
    triplePrice: Optional[MoneyVO] = None
    quadruplePrice: Optional[MoneyVO] = None
    tripleChildPercentageDiscount: Optional[float] = None
    quadrupleChildPercentageDiscount: Optional[float] = None

class PriceListEntry(BaseModel):
    """
    Confirmed against the real POST/PUT /closedtour/{supplierId}/{closedTourCode}
    schema. NOTE the field names: startDate/endDate (not from/to), and prices
    are nested MoneyVO objects (amount+currency), not flat numbers.
    """
    name: Optional[str] = None
    startDate: str  # ISO date "YYYY-MM-DD"
    endDate: str
    price: PriceListPriceVO

class ContractClosedTourOptionVO(BaseModel):
    id: Optional[int] = None
    code: str
    operationalDays: List[str] = WEEKDAY_NAMES.copy()  # weekday NAMES, e.g. "MONDAY" - confirmed real schema
    stopSales: List[StopSale] = []
    priceList: List[PriceListEntry] = Field(..., description="REQUIRED by the API - seasonal pricing matrix")
    translations: Dict[str, OptionTranslation] = {}
    quantityPerDay: int = 99
    onRequestQuantityPerDay: Optional[int] = None
    quantityPerDate: List[QuantityPerDate] = []
    onRequest: bool = True
    useAdditionalOnRequestQuota: bool = False

    @validator("priceList")
    def priceList_not_empty(cls, v):
        if not v:
            raise ValueError("priceList is required by Travel Compositor and cannot be empty")
        return v


# ==========================================
# 5. TICKET SCHEMAS (Excursions - single destination, no overnight)
# Confirmed field-by-field against real GET /tickets/{supplierId}/{ticketCode}
# and GET /tickets/{supplierId}/{ticketCode}/{optionCode} examples.
# ==========================================

class TicketHumanPreConfig(BaseModel):
    """Mirrors HumanPreConfig but for Tickets - separate since several fields don't apply (no pax-room concept)."""
    supplier_id: str = Field(..., example="48940")
    ticket_code: str = Field(..., example="JAP-T1", description="Human-chosen Ticket code")
    currency: str = Field(..., example="EUR")
    modality_code: str = Field(..., example="Standard")
    on_request: bool = Field(False, description="True for On Request, False for Instant Confirmation")
    days_available_before_release: int = Field(30)
    min_passengers: int = Field(1)
    max_passengers: int = Field(9)

    @validator("modality_code")
    def no_slash_in_modality_code(cls, v):
        if "/" in v or "\\" in v:
            raise ValueError("Modality Code cannot contain '/' or '\\' - it becomes part of a URL and breaks lookups")
        return v


class GeolocationVO(BaseModel):
    latitude: float
    longitude: float


class MeetingPointVO(BaseModel):
    description: str
    latitude: float
    longitude: float


class TicketDatasheetEN(BaseModel):
    """Only EN populated by this tool - confirmed real examples show many languages, but those are
    handled by Travel Compositor's own translation tooling, not generated by us."""
    name: str
    description: str  # HTML, same day-by-day-style rules don't apply (single description block)
    meetingPoint: str = ""
    departureTime: str = ""  # confirmed real field via fuller Swagger - display text, e.g. "8:00 AM"
    voucherRemarks: str = ""  # confirmed real field via fuller Swagger - shown on the customer's voucher
    includes: List[str] = []
    excludes: List[str] = []
    activityType: Optional[str] = None
    activityTypeId: Optional[str] = None
    languageOptions: List[str] = []


class TicketCancellationRange(BaseModel):
    """NOTE: field names are DIFFERENT from ClosedTour's CancellationRange (days/percentage) -
    confirmed via real data these are cancellationDays/cancellationPercentage for Tickets."""
    cancellationDays: int = 30
    cancellationPercentage: float = 100.0


class ApiStaticContentTicketVO(BaseModel):
    """Main Ticket payload - confirmed against real GET /tickets/{supplierId}/{ticketCode} response."""
    code: str
    name: str
    geolocation: GeolocationVO
    city: str
    zipCode: Optional[str] = None
    datasheets: Dict[str, TicketDatasheetEN]  # keyed "EN" only, by our tool's design choice
    currency: str = "EUR"
    # "Engines" (Settings > Engine > Select Search Engines To Sell in the TC admin UI) - confirmed via
    # a real screenshot to matter: a newly-created Ticket previously had NONE selected, requiring manual
    # fixing in the admin UI. This default includes the confirmed-valid enum values that plausibly match
    # what a real working Ticket had selected, excluding ones clearly tied to unrelated product types
    # (insurance, memberships, giftcards, cruises, holidays/packages, AI trip planning).
    productTypes: List[str] = [
        "MULTI", "ONLY_TICKET", "EVENT_TICKET", "ONLY_TRANSFER", "ONLY_TRAIN", "ONLY_HOTEL",
        "ONLY_HOUSE", "ONLY_FLIGHT", "FLIGHT_HOTEL", "FLIGHT_HOUSE", "ONLY_CAR", "GOLF",
        "MAGIC_BOX", "ROUTING", "PRIVATE_TOUR", "TRIP_PLANNER", "GROUPS",
    ]
    imageUrls: List[str] = []
    adultTaxesAmount: float = 0.0
    childTaxesAmount: float = 0.0
    infantTaxesAmount: float = 0.0
    daysAvailableBeforeRelease: int = 30
    releaseTime: int = 0
    releaseTimeType: str = "DAYS"
    modalityCodes: List[str] = []
    active: bool = False  # Same confirmed workflow as ClosedTour: must be True during creation of
                          # the first option, then switched back to False/draft afterward.
    duration: float = 0.0
    durationType: str = "HOURS"
    cancellationRanges: List[TicketCancellationRange] = [TicketCancellationRange()]
    locationOrigin: bool = True
    meetingPointOrigin: bool = True
    meetingPoints: List[MeetingPointVO] = []
    sendConfirmationMail: bool = True


class TicketSupplementTranslation(BaseModel):
    name: str


class TicketSupplementVO(BaseModel):
    """DIFFERENT structure from ClosedTour's SupplementVO - priced per passenger type,
    confirmed against the real Ticket Modality example."""
    adultPriceSupplement: float = 0.0
    childrenPriceSupplement: float = 0.0
    infantPriceSupplement: float = 0.0
    startDate: str
    endDate: str
    translations: Dict[str, TicketSupplementTranslation] = {}


class TicketRemark(BaseModel):
    name: str
    remarks: Optional[str] = None


class ContractTicketModalityVO(BaseModel):
    """Ticket Modality/Option payload - confirmed against real GET
    /tickets/{supplierId}/{ticketCode}/{optionCode} response.
    KEY DIFFERENCE from ClosedTour options: priced by PASSENGER TYPE (adult/child/infant),
    not room occupancy, and holds ONE price + ONE date range (not a seasonal priceList array).
    For seasonal/holiday pricing, use dated Supplements instead (confirmed approach)."""
    code: str
    operationalDays: List[str] = WEEKDAY_NAMES.copy()
    remarks: Dict[str, TicketRemark] = {}
    supplements: List[TicketSupplementVO] = []
    stopSales: List[StopSale] = []
    ticketsPerDay: int = 99
    disallowChildren: bool = False
    onRequest: bool = False
    disallowInfant: bool = False
    disallowAdult: bool = False
    startDate: str
    endDate: str
    baseServicePrice: float = 0.0
    baseAdultPrice: float = Field(..., description="REQUIRED - the core per-adult price")
    baseChildrenPrice: float = 0.0
    baseInfantPrice: float = 0.0
    maxPassengers: int = 9
    minPassengers: int = 1
    childAgeMin: int = 2  # infant = 0-2, child = 2-12 (confirmed international convention, unified with ClosedTour)
    childAgeMax: int = 12
    occupancyPrices: List[dict] = []
    priceType: str = "OCCUPANCY"
    onRequestRemarks: Dict[str, str] = {}
    languages: List[str] = ["EN"]
    timeTables: List[str] = []
    duration: float = 0.0
    durationType: str = "HOURS"


# ==========================================
# 6. TRANSFER SCHEMAS
# Confirmed field-by-field against the real Transfer Swagger (Contract -
# Transfer: GET/POST/PUT /transfer/{supplierId}, GET /transfer/{supplierId}/
# {transferId}) plus 13 real GET responses across 2 real suppliers
# (Hurghada/El Gouna point-to-point routes, and a Bali zone-based rate
# sheet) confirmed 2026-08. See builder.py for how DMC rate-sheet
# conventions (charge-unit-per-pax vs per-service, guide-language pricing,
# bracket occupancy tiers) map onto this shape.
# ==========================================

class TransferHumanPreConfig(BaseModel):
    """Mirrors TicketHumanPreConfig - human-supplied config a Transfer needs that
    isn't extractable from the supplier document itself."""
    supplier_id: str = Field(..., example="50696")
    currency: str = Field(..., example="EUR")
    days_available_before_release: int = Field(
        5, description="Confirmed real field name is releaseContract; confirmed real value seen in live data = 5."
    )


class TransferLocationVO(BaseModel):
    """Confirmed shape via real data: point-to-point suppliers (e.g. Hurghada) populate geolocation
    directly with raw coordinates; zone-based suppliers (e.g. Bali, where 'departure'/'arrival' are
    named AREAS covering several localities, not one GPS pin) should instead be resolved against the
    supplier's own Transfer Zones and use ContractTransferVO.departureLocationId/arrivalLocationId -
    see api_client.py's get_transfer_zones/resolve_transfer_zone_geolocation."""
    name: str
    geolocation: Optional[GeolocationVO] = None
    zoneRadius: Optional[float] = None


class TransferDescriptorVO(BaseModel):
    """Per-language datasheet entry (confirmed real fields via Swagger). Only EN populated by this
    tool - same convention as ClosedTour/Ticket; Travel Compositor's own translation tooling fills in
    other languages afterward (confirmed via one real example with ~30 languages populated)."""
    name: str
    description: str = ""
    pickupDescription: str = ""
    voucherRemarks: str = ""  # cancellation policy text + any location-conditional cost notes (e.g. harbor fee) go here


class TransferPropertyTranslation(BaseModel):
    description: str


class TransferPropertyVO(BaseModel):
    """Confirmed real shape, e.g. {"propertyType": "AIRCONDITION", "translations": {"EN": {"description": "Air Condition"}}}.
    Only AIRCONDITION/DOORTODOOR confirmed via real data so far - other enum values unconfirmed."""
    propertyType: str
    translations: Dict[str, TransferPropertyTranslation] = {}


class TransferAdditionalServiceTranslation(BaseModel):
    name: str


class TransferAdditionalServiceVO(BaseModel):
    """OPTIONAL/on-request extras only (child seat, non-default guide language, etc) - confirmed real
    shape via a live example ({"currency": "EUR", "maximum": 2, "price": 10.0, "translations": {"EN":
    {"name": "Child Seat"}}}). NOTE: flat single price only - no per-occupancy or per-duration
    variation is possible in this schema, so per-day supplier pricing collapses to one flat per-transfer
    charge (a transfer only runs a few hours, never multiple days - confirmed decision), and an "on
    request" qualifier from the source document must be folded directly into the name text (e.g. "Child
    Seat (on request)"), since there is no structured on-request flag here."""
    currency: str = "EUR"
    maximum: int = 1
    price: float = 0.0
    translations: Dict[str, TransferAdditionalServiceTranslation] = {}


class TransferMoneyVO(BaseModel):
    """Mirrors the real GET response's per-occupancy Money object. The naN/zero/negative/positive/etc
    boolean flags seen on real GET responses are server-computed/derived - never set them on write,
    only amount+currency are meaningful here."""
    amount: float = 0.0
    currency: str = "EUR"


class TransferOccupancyPriceVO(BaseModel):
    """Confirmed real shape via pricesByOccupancy. CONFIRMED SEMANTICS (per product-owner clarification):
    the top-level ContractTransferVO.basePrice is the DEFAULT per-occupancy rate; an entry here is only
    needed for an occupancy whose rate genuinely DIFFERS from that default (e.g. a real example had
    basePrice=11 covering occupancy 2-4, with only occupancy=1 listed here at double that rate as a
    solo-traveler surcharge) - this is NOT a fixed/derived duplicate of basePrice, despite every
    single-supplier example so far happening to show a clean 2x ratio. When a document gives a fully
    explicit rate for every occupancy bracket (no implicit "default"), write an explicit entry for each
    bracket instead of relying on any fallback - see builder.py."""
    occupancy: int
    basePrice: TransferMoneyVO
    childPrice: TransferMoneyVO = TransferMoneyVO()
    infantPrice: TransferMoneyVO = TransferMoneyVO()
    priceByPax: bool = True
    onRequest: bool = False


class TransferSupplementVO(BaseModel):
    """MANDATORY, automatically-applied surcharges ONLY (confirmed product-owner rule) - e.g. a genuine
    date/time-based surcharge. NEVER used for optional/on-request extras (those belong in
    additionalServices instead) and NEVER for anything conditional on WHICH pickup point was used within
    a broader zone (e.g. a harbor-only pickup fee) - this schema can only condition a supplement on a
    date/time window (startDate/endDate/startTime/endTime), not on location, so a location-conditional
    fee applied here would incorrectly charge every booking on the route, including the common case
    (e.g. airport pickup) that shouldn't be charged it.

    type: PERCENT / ABSOLUTE. CONFIRMED PRODUCT-OWNER RULE: a transfer supplement can be expressed
    either way, and for a percentage Travel Compositor applies it to the base price ITSELF - the app
    sends amount=50 with type=PERCENT for "50% night surcharge" and must NEVER pre-calculate 50% into
    a currency figure. Pre-calculating would freeze the surcharge at one occupancy's price and then
    silently under- or over-charge every other group size.

    NOT YET VERIFIED AGAINST A LIVE TRANSFER: every real GET example seen so far has this list empty,
    so the enum's exact spelling is taken from the Hotel supplement enum (the only place Travel
    Compositor spells it out). Worth confirming on one test transfer before relying on it.

    TIME WINDOW: startTime/endTime carry the surcharge's hours (e.g. 22:00 -> 08:00 for a night
    surcharge) and MAY legitimately wrap past midnight. startDate/endDate carry its validity, which
    defaults to the parent transfer's own validity window when the document states no dates."""
    active: bool = True
    name: str = ""
    startDate: Optional[str] = None
    endDate: Optional[str] = None
    startTime: Optional[str] = None
    endTime: Optional[str] = None
    type: str = "ABSOLUTE"  # PERCENT / ABSOLUTE
    amount: float = 0.0


class TransferOperationalDayVO(BaseModel):
    """Confirmed real shape - fromHour/toHour are optional and omitted in every real example seen."""
    operationalDays: str  # one of WEEKDAY_NAMES
    fromHour: Optional[str] = None
    toHour: Optional[str] = None


class ContractTransferVO(BaseModel):
    """Main Transfer payload - confirmed field-by-field against real Swagger + 13 real
    GET /transfer/{supplierId}[/{transferId}] examples across 2 suppliers.

    KEY DIFFERENCE from ClosedTour/Ticket: Travel Compositor assigns 'id' itself
    (format "TRANSFER-412545") - there is NO human-assigned code field anywhere in
    this schema, so recognizing which existing transfer to update on a supplier
    rate refresh cannot rely on a code the way ClosedTour (providerCode) or Ticket
    (code) do. See transfer_matcher.py for the confirmed matching strategy
    (app-tracked id as primary key, departure/arrival similarity as a human-
    confirmed fallback).

    ALSO KEY DIFFERENCE: PUT /transfer/{supplierId} does NOT take the id in the
    URL path (unlike ClosedTour/Ticket's PUT) - the id must be set on this
    payload's own 'id' field for an update; leave it None for a create.
    """
    active: bool = True
    id: Optional[str] = None
    name: str
    productType: str = "ECONOMY"  # ECONOMY/STANDARD/EXPRESS/SPECIAL/PREMIUM/LUXURY
    serviceType: str = "PRIVATE"  # PRIVATE/SHUTTLE/SHARED
    vehicleType: str = "CAR"
    departure: TransferLocationVO
    arrival: TransferLocationVO
    departureLocationId: Optional[int] = None  # Transfer Zone id - populated for zone-based (area) routing
    arrivalLocationId: Optional[int] = None
    pickupInformation: Optional[str] = None
    datasheets: Dict[str, TransferDescriptorVO]  # keyed "EN" only, by our tool's design choice
    images: List[str] = []
    properties: List[TransferPropertyVO] = []
    startDate: str  # real season validity as stated in the supplier document (confirmed decision - NOT a fixed far-future default)
    endDate: str
    releaseContract: int = 5
    currency: str = "EUR"
    basePrice: float = 0.0  # the default per-occupancy rate - see TransferOccupancyPriceVO's docstring
    maxOccupancy: int = 4
    minOccupancy: int = 1
    maxVehicles: int = 4
    allowMultipleVehicles: bool = True
    operationalDaysWithHours: List[TransferOperationalDayVO] = [
        TransferOperationalDayVO(operationalDays=d) for d in WEEKDAY_NAMES
    ]
    priceByPax: bool = True
    pricesByOccupancy: List[TransferOccupancyPriceVO] = []
    supplements: List[TransferSupplementVO] = []  # MANDATORY charges only - see TransferSupplementVO's docstring
    stopSales: List[StopSale] = []
    additionalServices: List[TransferAdditionalServiceVO] = []  # OPTIONAL/on-request extras only


# ==========================================
# 7. TRANSPORT SCHEMAS
# Confirmed field-by-field against the real Transport Swagger (Contract -
# Transport: GET/POST/PUT /transport/{supplierId}, GET/POST/PUT
# /transport/{supplierId}/{transportId}[/{optionCode}]) plus real GET
# responses across 2 real suppliers/routes (Aswan-Hurghada CAR point-to-
# point with 2 occupancy brackets, Praslin-La Digue COMBINED car+ferry+car
# route with 4 occupancy brackets) confirmed 2026-08. See builder.py for
# how DMC rate-sheet conventions (per-vehicle/per-passenger/per-occupancy
# pricing, occupancy brackets as separate Option sub-resources) map onto
# this shape.
# ==========================================

class TransportHumanPreConfig(BaseModel):
    """Mirrors TransferHumanPreConfig - human-supplied config a Transport needs that isn't
    extractable from the supplier document itself."""
    supplier_id: str = Field(..., example="50696")
    currency: str = Field(..., example="EUR")
    days_available_before_release: int = Field(
        5, description="Confirmed real field name is releaseContract; real values seen in live data range 5-14."
    )


class TransportSegmentVO(BaseModel):
    """Confirmed real shape via ContractTransportSegmentVO. IMPORTANT: segments do NOT map
    one-to-one to each physical leg of a multi-modal journey - a real COMBINED (car+public
    ferry+car) route was still represented as a SINGLE segment covering the whole departure-to-
    arrival span, with the individual legs only described in free text (datasheets.description),
    not as structured per-leg data. durationTime is also NOT simply arrivalTime-minus-
    departureTime - a real example showed a 6.5 hour departure-to-arrival window but a 1.5 hour
    durationTime, suggesting durationTime tracks active travel time (e.g. just the ferry
    crossing) while the full window includes waiting time between legs; never derive one from
    the other. model/numService (vehicle/aircraft/train model, and service/flight number) were
    blank in every CAR example but should be populated when a document states them (e.g. a real
    flight/train number)."""
    departureLocationCode: str  # a Transport Base code - see api_client.resolve_transport_base()
    arrivalLocationCode: str
    departureTime: str  # "HH:MM:SS"
    arrivalTime: str  # "HH:MM:SS"
    plusDays: int = 0
    durationTime: Optional[str] = None
    model: Optional[str] = None
    numService: Optional[str] = None


class TransportDataSheetVO(BaseModel):
    """Shared shape for BOTH the main transport's per-language datasheets AND an option's per-
    language translations (confirmed identical ContractTransportDataSheetVO type in the Swagger
    for both). Only EN populated by this tool - Travel Compositor's own translation tooling
    fills in the ~30 other languages afterward (confirmed via real examples). NOTE: real option
    translations only ever populate `name`, never `description` - description is optional here
    specifically to support that case without sending a meaningless empty string."""
    name: str
    description: Optional[str] = None


class ContractTransportCancellationRangeVO(BaseModel):
    """Confirmed real shape - unlike Transfer, Transport has a genuine structured cancellation
    field (no text-only fallback needed here)."""
    days: int = 30
    percentage: float = 100.0  # REFUND %, same convention as CancellationRange/TicketCancellationRange
    isBeforeStart: bool = True


class LocalDateRangeVO(BaseModel):
    """Confirmed real shape for an option's inventoryDate. CONFIRMED RULE (product owner):
    "most of the inventory from Transfer and Transports will be automatically set to 2049, as
    we want to make the products available at all time" - end defaults to the standing far-
    future convention rather than being derived from the document."""
    start: str
    end: str = "2049-12-31"


class ContractTransportOptionInventoryVO(BaseModel):
    """Confirmed real shape. `quantity` has shown 0 in every real example seen so far - product
    owner confirmed the inventory DATE range is what actually matters (the 2049 "always
    available" convention); quantity's precise operational meaning is unconfirmed but
    consistently 0, so that's used as the safe default rather than guessing at something else."""
    inventoryDate: LocalDateRangeVO
    quantity: int = 0


class ContractTransportOptionPriceVO(BaseModel):
    """Confirmed real shape AND semantics (product owner, corrected from an initial wrong guess):
    these are ADDITIVE SURCHARGES on top of the parent ContractTransportVO's base price fields,
    NOT alternate/override rates - final price for this bracket = parent's baseAdultPrice (etc)
    + this entry's adultPriceSupplement (etc). A bracket that costs exactly the base rate simply
    has NO price entries at all (confirmed via a real 2-9 pax bracket with prices=[]), rather
    than a redundant entry equal to the base. CONFIRMED: no reliable formula/curve exists between
    different brackets' supplements (a real 4-bracket example showed 64/43/64/43, non-monotonic)
    - never interpolate or derive one bracket's supplement from another; always take the
    document's own stated number for that specific bracket."""
    name: Optional[str] = None
    startDate: str
    endDate: str = "2049-12-31"
    adultPriceSupplement: float = 0.0
    childrenPriceSupplement: float = 0.0
    infantPriceSupplement: float = 0.0
    adultRTPriceSupplement: float = 0.0
    childrenRTPriceSupplement: float = 0.0
    infantRTPriceSupplement: float = 0.0


class ContractTransportOptionVO(BaseModel):
    """Main Option payload (a single occupancy/passenger bracket) - confirmed field-by-field
    against real Swagger + 4 real GET /transport/{supplierId}/{transportId}/{optionCode}
    examples across 2 transports. CONFIRMED STRUCTURE (product owner): per-occupancy pricing for
    Transport is modelled as SEPARATE OPTION SUB-RESOURCES, one per bracket, each with its own
    minPassengers/maxPassengers range and price supplement(s) - NOT an array field on the parent
    the way Transfer's pricesByOccupancy works. Real option codes are NOT predictable/derivable
    from the route name (confirmed real examples: "ASWHRG", "PraslinLaDigue12" alongside ones
    that literally equal the transport's own name) - this tool generates its own codes on create.
    cabinClassType defaults to ECONOMY even for non-flight transports (confirmed real CAR
    example) - it's a generic service-tier field, not literally about airline cabins.
    baggageAllowance/baggageAllowanceType and agencyId have never been populated in any real
    example seen - left as optional pass-through fields rather than guessed at."""
    code: str
    active: bool = True
    cabinClassType: str = "ECONOMY"  # BUSINESS/FIRST/PREMIUM_ECONOMY/ECONOMY/PREFERRED/TOURIST_PLUS/TOURIST
    baggageAllowance: Optional[str] = None
    baggageAllowanceType: Optional[str] = None  # KG/PC
    minPassengers: int = 1
    maxPassengers: int = 1
    onRequest: bool = False
    agencyId: Optional[str] = None
    prices: List[ContractTransportOptionPriceVO] = []
    inventories: List[ContractTransportOptionInventoryVO] = []
    translations: Dict[str, TransportDataSheetVO] = {}


# CONFIRMED REAL DEFAULT (product owner): same pattern already established for Ticket's
# product_types ("Engines") field - a curated fixed list, never AI-extracted from the source
# document. Strong evidence this specific list is the platform default rather than something
# that varies per-transport: two completely different real transports (different suppliers,
# different routes, different transportType - CAR vs COMBINED) both showed this EXACT same list.
_TRANSPORT_DEFAULT_PRODUCT_TYPES = [
    "ONLY_FLIGHT", "ONLY_TRAIN", "FLIGHT_HOTEL", "FLIGHT_HOUSE", "MULTI",
    "GOLF", "MAGIC_BOX", "ROUTING", "CRUISES", "TRIP_PLANNER",
]


class ContractTransportVO(BaseModel):
    """Main Transport payload - confirmed field-by-field against real Swagger + real
    GET /transport/{supplierId}[/{transportId}] examples across 2 suppliers/routes.

    KEY DIFFERENCE from ClosedTour/Ticket: like Transfer, Travel Compositor assigns 'id' itself
    (format "TRANSPORT-412579") - there is no human-assigned code, so recognizing which existing
    transport to update on a rate refresh needs the same app-tracked-id + route-similarity
    matching strategy as transfer_matcher.py - see transport_matcher.py.

    ALSO KEY DIFFERENCE: PUT /transport/{supplierId} does NOT take the id in the URL path (same
    as Transfer) - the id must be set on this payload's own 'id' field for an update.

    airlineCode is marked REQUIRED in the Swagger (even for non-flight transportTypes like CAR/
    COMBINED) but was ABSENT from every real GET example seen, including CAR/COMBINED ones -
    defaulted to "" here since no real example clarifies what a non-flight transport should send;
    flag this if a real create/update is ever rejected specifically for this field.

    minChildAge/maxChildAge/minInfantAge/maxInfantAge default to 2/11/0/2 here (NOT the 2/12
    convention used elsewhere in this app for ClosedTour/Ticket) - confirmed real value in both
    real Transport examples seen.
    """
    active: bool = True
    id: Optional[str] = None
    name: str
    airlineCode: str = ""
    segments: List[TransportSegmentVO]
    transportType: str = "CAR"  # CAR/PLANE/COMBINED confirmed real+placeholder values; full enum (8 values) unconfirmed
    datasheets: Dict[str, TransportDataSheetVO]
    images: List[str] = []
    productTypes: List[str] = _TRANSPORT_DEFAULT_PRODUCT_TYPES.copy()
    pricePerPax: bool = True
    currency: str = "EUR"
    vehiclePrice: float = 0.0
    baseAdultPrice: float = 0.0
    baseChildrenPrice: float = 0.0
    baseInfantPrice: float = 0.0
    baseAdultRTPrice: float = 0.0
    baseChildrenRTPrice: float = 0.0
    baseInfantRTPrice: float = 0.0
    adultTaxesAmount: float = 0.0
    childrenTaxesAmount: float = 0.0
    infantTaxesAmount: float = 0.0
    adultRTTaxesAmount: float = 0.0
    childrenRTTaxesAmount: float = 0.0
    infantRTTaxesAmount: float = 0.0
    startDate: str
    endDate: str
    releaseContract: int = 5
    operationalDays: List[str] = WEEKDAY_NAMES.copy()
    optionCodes: List[str] = []
    onlyHolidayPackage: bool = False
    showInTransportQuotasLanding: bool = False
    minChildAge: int = 2
    maxChildAge: int = 11
    minInfantAge: int = 0
    maxInfantAge: int = 2
    allowOWPrice: bool = True
    allowRTPrice: bool = False  # RT deprioritized (product owner) - no real example has RT enabled
    minStayNights: int = 0
    maxStayNights: int = 0
    combinableAsInboundRTPrice: bool = False
    companyName: str = ""
    cancellationRanges: List[ContractTransportCancellationRangeVO] = [ContractTransportCancellationRangeVO()]
    combinableRtContracts: List[str] = []


# ==========================================
# 8. HOTEL SCHEMAS
# Confirmed field-by-field against the real Hotel Swagger (Contract - Hotel:
# GET/POST /hotel/{supplierId}, GET /hotel/{supplierId}/{providerCode}, PUT
# /hotel/{supplierId}, POST /hotel/mealplan|offer|rates|room|supplement/
# {supplierId}/{providerCode}, PUT /hotel/rates/{supplierId}/{providerCode})
# plus a real GET response for a live hotel (CAI-H1 / Four Seasons Hotel
# Cairo at Nile Plaza, supplier 48940) pulled twice.
#
# STRUCTURE, unlike every other product type built so far: ONE parent hotel
# record (created via POST/PUT /hotel/{supplierId}, carrying its rooms[] and
# mealPlans[] inline) plus THREE separate sibling sub-resource families -
# Offers and Supplements (both CREATE-ONLY, no PUT endpoint confirmed to
# exist - see their docstrings below) and Rates (which DOES have both POST
# and PUT, and itself nests Seasons, which nest per-room Distribution
# pricing AND per-room Stop Sales).
# ==========================================
class HotelHumanPreConfig(BaseModel):
    """Mirrors TransportHumanPreConfig/TransferHumanPreConfig - human-supplied config a Hotel
    needs that isn't extractable from the supplier document itself. Unlike Transport, a HOTEL's
    providerCode is human-assigned (confirmed real example: "CAI-H1"), not Travel Compositor-
    generated - so it's supplied here up front rather than coming back from a create call."""
    supplier_id: str = Field(..., example="48940")
    provider_code: str = Field(..., example="CAI-H1", description="Human-assigned hotel code (confirmed real format like 'CAI-H1'). Required for every call - GET/POST/PUT all key off this.")
    currency: str = Field(..., example="USD")
    days_available_before_release: int = Field(7, description="Confirmed real field name is releaseDays; real value seen in live data was 7.")


class HotelAddressVO(BaseModel):
    """Confirmed real shape via HotelAddressVO."""
    address: Optional[str] = None
    locationName: Optional[str] = None
    postalCode: Optional[str] = None
    country: Optional[str] = None
    phone: Optional[str] = None
    fax: Optional[str] = None
    email: Optional[str] = None


class TranslationVO(BaseModel):
    """Confirmed real shape - {language, description} pairs, used for the Hotel's own
    descriptions/voucherRemarks AND for Offer/Supplement 'names'. Only EN populated by this
    tool, same convention as every other product type - Travel Compositor's own translation
    tooling fills in the ~40 other languages afterward."""
    language: str = "EN"  # confirmed 40-value enum (EN, EN_IE, EN_US, ES, IT, FR, PT, PT_BR, AR, RO, EL, FI, DE, NL, SV, ZH, ZH_TW, RU, HU, FA, PL, CA, BG, JA, MS, NO, TR, SK, SL, CS, HR, AZ, HE, DA, TH, SQ, KA, SR, UZ, EU) - loose string, not strictly validated, same convention as Currency/DayOfWeek elsewhere in this app
    description: str = ""


class ContractRoomDistributionVO(BaseModel):
    """Confirmed real shape - an ALLOWED occupancy combination for a room (no price attached -
    see ContractRoomDistributionPriceVO for the priced version used in seasonRoomPrices). Matches
    the on/off grid in Travel Compositor's own 'Distribution allowed' room UI."""
    adults: int = Field(..., ge=1)
    children: int = Field(0, ge=0)


class ContractRoomVO(BaseModel):
    """Confirmed real shape via ContractRoomVO (both the POST /hotel/room body and the nested
    rooms[] entries on the hotel itself). Only 'distributions' is actually required in the
    Swagger (marked with *) - name/typeId/providerCode are all optional, and a real live example
    (Four Seasons Hotel Cairo) had typeId completely unset on both its real rooms, confirming
    it's safe to omit rather than guess at a value.

    providerCode: CONFIRMED (product owner) - unlike the HOTEL's own providerCode (human-
    assigned), a ROOM's providerCode is system-generated (real examples: "AUTO_jr9fFXzBSX1YlVmT
    LVOw8PuP") - "I don't set any other AUTO code to it." Leave unset on create and capture
    whatever Travel Compositor assigns back for our own tracking (see hotel_matcher.py) - never
    invent one ourselves.

    typeId: optional passthrough (string) - no confirmed master-list reference found in either
    Swagger group explored (Contract Hotel or Web Content Accommodations); left None unless a
    document/human gives us something concrete to put there."""
    name: Optional[str] = None
    typeId: Optional[str] = None
    providerCode: Optional[str] = None
    distributions: List[ContractRoomDistributionVO]


class ContractMealPlanVO(BaseModel):
    """Confirmed real shape. mealPlan is a fixed 5-value enum (ROOM_ONLY, BED_AND_BREAKFAST,
    HALF_BOARD, FULL_BOARD, ALL_INCLUSIVE) - document meal-plan wording gets mapped onto these
    rather than passed through as free text.

    CONFIRMED REAL RULE (product owner): "If no other stated, the Room only is always taken as 0
    money, many hotel say breakfast optional and then we must add the breakfast per night to the
    meal plan on the top of the 0 money." -> ROOM_ONLY always basePrice=0/adultPrices=[]/
    childPrices=[]; any other plan is a genuine per-night add-on cost.

    ASSUMPTION, NOT independently confirmed against a real populated example (flagged, same as
    Transport's airlineCode): basePrice = the cost for the 1st adult, and adultPrices[i]/
    childPrices[i] = the incremental cost for each ADDITIONAL adult/child beyond the first (index
    0 = 2nd adult, index 1 = 3rd adult, etc; childPrices index 0 = 1st child, index 1 = 2nd
    child). Verify against a real populated meal-plan example before relying on this for a live
    upload with non-zero add-on prices."""
    mealPlan: str  # ROOM_ONLY / BED_AND_BREAKFAST / HALF_BOARD / FULL_BOARD / ALL_INCLUSIVE
    basePrice: float = 0.0
    adultPrices: List[float] = []
    childPrices: List[float] = []


class ContractRoomDistributionPriceVO(BaseModel):
    """Confirmed real shape - the PRICED counterpart to ContractRoomDistributionVO, used inside
    seasonRoomPrices.distributionPrices for the DISTRIBUTION pricing model. CONFIRMED (product
    owner): the suspiciously perfect arithmetic seen between brackets in the one real example
    pulled (+200/adult, +100/child) is DEMO DATA ONLY, not a real pricing formula - "distribution
    Price is just for Demo." Same discipline as Transport's occupancy brackets: every amount must
    be extracted literally from the real document, never interpolated from a pattern."""
    amount: float
    adults: int
    children: int = 0


class ContractHotelSeasonPricesVO(BaseModel):
    """Confirmed real shape - one entry per ROOM within a season (providerRoomCode links back to
    a ContractRoomVO). CONFIRMED REAL DEFAULTS (product owner): "Quota means how many rooms we
    are having allotments (if nothing mentioned please make 20 Quota and if no request set,
    please leave on request 0)."

    basePrice/adultPrices/childPrices here are for the PAX pricing model (priceType=PAX on the
    parent season) - UNCONFIRMED against a real populated example (every real season pulled used
    DISTRIBUTION pricing) - same nth-additional-occupant indexing assumption as ContractMealPlanVO
    applies here if PAX pricing is ever used; flag and verify before relying on it."""
    unitsQuota: int = 20
    unitsOnRequest: int = 0
    providerRoomCode: str
    distributionPrices: List[ContractRoomDistributionPriceVO] = []
    basePrice: float = 0.0
    adultPrices: List[float] = []
    childPrices: List[float] = []


class ContractHotelSeasonVO(BaseModel):
    """Confirmed real shape. dateRanges supports multiple non-contiguous ranges under one season
    (confirmed real example: a single season covering both Oct 1-8 AND Oct 15-Jan 31). CONFIRMED
    BY OMISSION: there is no operationalDays/checkInDays/checkOutDays field anywhere on this VO
    (only Offer/Supplement carry operationalDays) - any day-of-week restriction mentioned in a
    document has nowhere structured to go here and should be folded into descriptive text
    instead, same treatment as Transport's unstructured surcharge notes."""
    id: Optional[int] = None  # server-assigned - omit on create, supply the existing value to update in place
    name: str
    dateRanges: List[LocalDateRangeVO]
    mealPlans: List[ContractMealPlanVO] = []
    seasonRoomPrices: List[ContractHotelSeasonPricesVO] = []
    releaseDays: Optional[int] = None
    minimumStay: int = 1
    maximumStay: Optional[int] = None
    priceType: str = "DISTRIBUTION"  # PAX / DISTRIBUTION - confirmed 2-value enum


class ContractHotelRoomStopSalesVO(BaseModel):
    """Confirmed real shape via a real populated example (stop sales for 'Superior Room' and
    'Premium Superior Room', each with their own blackout date ranges). IMPORTANT UNRESOLVED GAP
    (jointly flagged with the product owner): roomId is a numeric id that is NEVER returned
    anywhere by the Contract Hotel API - not in the room creation response, not in the room's own
    representation nested inside the full hotel GET - it only ever appears inside an ALREADY-
    EXISTING stopSales entry. Neither roomId nor roomName is marked required in the Swagger
    (unlike distributions* on the room itself), which is the basis for this tool's working
    assumption: submit stop-sales using roomName ONLY (which this tool always has, since it's the
    name chosen at room-creation time) and leave roomId unset, trusting Travel Compositor resolves
    the match server-side. THIS IS UNCONFIRMED AND NEEDS A LIVE VALIDATION TEST before being
    relied on for a real upload - if rejected, stop-sales needs a different resolution path."""
    roomId: Optional[int] = None
    roomName: Optional[str] = None
    stopSales: List[LocalDateRangeVO] = []


class ContractHotelRateVO(BaseModel):
    """Confirmed real shape via real Swagger + a real populated GET example. CONFIRMED REAL RULE
    (product owner): "No deleting needed for rates, if the time window is closed, it is done then
    and it cant be sold anymore, so no harm if not deleted." - same date-bounded self-expiry
    reasoning already established for Transfer/Transport being exempt from the active:false
    staleness rule, just via a different mechanism (date-bounded here, vs flexible vehicle
    booking there) - no deactivation logic needed for rates/seasons.

    id: server-assigned integer - omit on create (POST /hotel/rates), supply the existing value
    to update in place (PUT /hotel/rates).

    offers/supplements here are just PROVIDER CODE STRINGS (confirmed real example: "AUTO_ziuMTf
    6PqnyO1w1DspPPxkaQ") referencing Offers/Supplements already registered at the HOTEL level
    (see ContractHotelOffersVO/ContractHotelSupplementVO) - a rate does not define its own
    offer/supplement content, it only links to ones that already exist."""
    id: Optional[int] = None
    name: str
    bookingWindows: List[LocalDateRangeVO] = []
    seasons: List[ContractHotelSeasonVO] = []
    offers: List[str] = []
    supplements: List[str] = []
    stopSales: List[ContractHotelRoomStopSalesVO] = []
    releaseDays: Optional[int] = None
    minimumStay: int = 1
    maximumStay: Optional[int] = None


class ContractHotelOffersVO(BaseModel):
    """Confirmed real shape via real Swagger + a real populated example ("10% Discount when stay
    3 or more days"). CONFIRMED NO PUT ENDPOINT EXISTS (only POST /hotel/offer/{supplierId}/
    {providerCode}) - offers are create-only. Since every offer is itself date-bounded
    (travelWindows/bookingWindows), the same self-expiry reasoning as Rates applies: a fresh
    document's offers are always created anew rather than update-matched against existing ones;
    this tool does light dedup (by names[0].description) purely to avoid re-creating an
    identical-looking offer within the same run, not a true update path.

    type: PERCENT / ABSOLUTE / STAY_TO_PAY (confirmed 3-value enum) - stay/pay only populated for
    STAY_TO_PAY, value/childValue only for PERCENT/ABSOLUTE.

    apply: confirmed 7-value enum (LODGING, MEAL, LODGING_AND_MEAL, PER_NIGHT, PER_NIGHT_PERSON,
    PER_STAY, PER_STAY_PERSON) mixing TWO dimensions (what it applies to vs how it's calculated)
    into one flat field - which of the 7 is correct depends entirely on how the specific document
    phrases the offer, not something derivable from a formula.

    providerCode: system-generated (AUTO_... - same convention as ContractRoomVO), never set by
    this tool on create."""
    providerCode: Optional[str] = None
    type: str  # PERCENT / ABSOLUTE / STAY_TO_PAY
    apply: str  # LODGING / MEAL / LODGING_AND_MEAL / PER_NIGHT / PER_NIGHT_PERSON / PER_STAY / PER_STAY_PERSON
    releaseDays: Optional[int] = None
    minimumStay: Optional[int] = None
    maximumStay: Optional[int] = None
    minimumAdults: Optional[int] = None
    maximumAdults: Optional[int] = None
    minimumChildrens: Optional[int] = None
    maximumChildrens: Optional[int] = None
    stay: Optional[int] = None
    pay: Optional[int] = None
    value: float = 0.0
    childValue: float = 0.0
    names: List[TranslationVO] = []
    travelWindows: List[LocalDateRangeVO] = []
    bookingWindows: List[LocalDateRangeVO] = []
    providerRoomCodes: List[str] = []
    mealPlans: List[str] = []
    operationalDays: List[str] = WEEKDAY_NAMES.copy()


class ContractHotelSupplementVO(BaseModel):
    """Confirmed real shape via real Swagger + a real populated example ("Special Event Charge").
    Same create-only / no-PUT-endpoint situation as Offers - see ContractHotelOffersVO's
    docstring, identical reasoning applies here.

    type: PERCENT / ABSOLUTE (confirmed 2-value enum - no STAY_TO_PAY option for supplements,
    unlike offers, since a supplement is always a straightforward extra charge).
    apply: same confirmed 7-value enum as Offers."""
    providerCode: Optional[str] = None
    type: str  # PERCENT / ABSOLUTE
    apply: str  # LODGING / MEAL / LODGING_AND_MEAL / PER_NIGHT / PER_NIGHT_PERSON / PER_STAY / PER_STAY_PERSON
    releaseDays: Optional[int] = None
    minimumStay: Optional[int] = None
    maximumStay: Optional[int] = None
    minimumAdults: Optional[int] = None
    maximumAdults: Optional[int] = None
    minimumChildrens: Optional[int] = None
    maximumChildrens: Optional[int] = None
    value: float = 0.0
    childValue: float = 0.0
    names: List[TranslationVO] = []
    travelWindows: List[LocalDateRangeVO] = []
    bookingWindows: List[LocalDateRangeVO] = []
    providerRoomCodes: List[str] = []
    mealPlans: List[str] = []
    operationalDays: List[str] = WEEKDAY_NAMES.copy()


class ContractHotelVO(BaseModel):
    """Main Hotel payload - confirmed field-by-field against the real Swagger (ContractHotel
    DetailedVO for POST/PUT /hotel/{supplierId}) + 2 real GET pulls for a live hotel (CAI-H1,
    Four Seasons Hotel Cairo at Nile Plaza, supplier 48940).

    KEY DIFFERENCE from every other product type: providerCode is HUMAN-ASSIGNED (confirmed:
    "CAI-H1"), not Travel Compositor-generated. So unlike Transfer/Transport, there's no id-in-
    body update quirk and no route-similarity matching needed to recognize an existing hotel -
    providerCode itself, supplied up front via HotelHumanPreConfig, is the stable identifier for
    both create and update.

    rooms/mealPlans are INLINE on this record (confirmed required, min 1 item each in the
    Swagger) - unlike Offers/Supplements/Rates, which are separate sub-resource endpoints. PUT
    replaces the whole record including the full rooms/mealPlans arrays (same "full replace"
    semantics as Transfer/Transport's PUT) - see build_hotel_payloads()'s merge-on-update logic
    for how existing rooms/mealPlans not mentioned in a fresh document are preserved rather than
    silently dropped.

    minimumChildrenAge/maximumChildrenAge: CONFIRMED (product owner) this API only supports ONE
    age range, unlike the Travel Compositor admin UI's own up-to-4-range widget - "if you can not
    divide it then we must make child from 0 to 12" - defaults to a single combined 0-12 band
    covering both infant and child ages together, unless a document states a narrower range
    explicitly.

    infantsAllowed: CONFIRMED an integer CAPACITY (max infants per booking), not an age boundary -
    genuinely decoupled from minimumChildrenAge/maximumChildrenAge. The real live example used 2;
    defaulted to the same value here as a reasonable starting point, flagged as an assumption for
    any hotel that doesn't state its own infant capacity.

    NO STRUCTURED CANCELLATION FIELD EXISTS anywhere on this VO (confirmed - no cancellationRanges
    or equivalent, unlike ClosedTour/Ticket/Transport) - cancellation policy text goes into
    voucherRemarks only, via the shared _cancellation_voucher_text() helper, same as Transfer's
    text-only fallback."""
    providerCode: str
    hotelname: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    address: HotelAddressVO = HotelAddressVO()
    category: str = ""
    chain: Optional[str] = None
    currency: str = "EUR"
    releaseDays: int = 7
    minimumStay: int = 1
    maximumStay: Optional[int] = None
    infantsAllowed: int = 2
    minimumChildrenAge: int = 0
    maximumChildrenAge: int = 12
    rooms: List[ContractRoomVO]
    mealPlans: List[ContractMealPlanVO]
    descriptions: List[TranslationVO] = []
    voucherRemarks: List[TranslationVO] = []
    images: List[str] = []