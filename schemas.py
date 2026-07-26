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
    user_id: str = "momiratravel-Christian"
    min_child_age: int = 0
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
    userId: str = "momiratravel-Christian"
    code: str
    providerCode: str
    name: str
    datasheets: Dict[str, DatasheetEN]
    images: List[str] = []
    itinerary: List[ItineraryItem] = []
    startTime: str = ""
    endTime: str = ""
    minChildAge: int = 0
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
    active: bool = False  # LOCKED to False for draft upload
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
