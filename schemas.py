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
    (e.g. airport pickup) that shouldn't be charged it. Real examples always show this empty - the
    `type` enum's real values are unconfirmed, "AMOUNT" is a placeholder default pending confirmation."""
    active: bool = True
    name: str = ""
    startDate: Optional[str] = None
    endDate: Optional[str] = None
    startTime: Optional[str] = None
    endTime: Optional[str] = None
    type: str = "AMOUNT"
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
