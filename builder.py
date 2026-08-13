
# Stamped on every delivery. app.py compares this against its own build string and says
# so on screen when they differ - a partial push (one file committed, another not) used to
# surface only as a traceback whose line numbers pointed at unrelated code.
MODULE_BUILD = "2026-08-13-ticket-occupancy-only-pricing"

import math
import datetime
import re
from typing import Dict, Any, List
from pydantic import ValidationError
from schemas import HumanPreConfig, ContractClosedTourVO, build_datasheets, DatasheetEN, ItineraryItem, ContractClosedTourOptionVO, WEEKDAY_NAMES, SupplementVO, SupplementPriceVO, SupplementTranslation, OptionTranslation, CancellationRange
from schemas import TicketHumanPreConfig, ApiStaticContentTicketVO, ContractTicketModalityVO, GeolocationVO, MeetingPointVO, TicketDatasheetEN, TicketCancellationRange, TicketSupplementVO, TicketSupplementTranslation, TicketRemark
from schemas import TransferHumanPreConfig, ContractTransferVO, TransferLocationVO, TransferDescriptorVO, TransferAdditionalServiceVO, TransferAdditionalServiceTranslation, TransferMoneyVO, TransferOccupancyPriceVO, TransferSupplementVO, TransferPropertyVO, TransferPropertyTranslation
from schemas import TransportHumanPreConfig, ContractTransportVO, TransportSegmentVO, TransportDataSheetVO, ContractTransportCancellationRangeVO, ContractTransportOptionVO, ContractTransportOptionPriceVO, ContractTransportOptionInventoryVO, LocalDateRangeVO
from schemas import HotelAddressVO, TranslationVO, ContractRoomDistributionVO, ContractRoomVO, ContractMealPlanVO, ContractRoomDistributionPriceVO, ContractHotelSeasonPricesVO, ContractHotelSeasonVO, ContractHotelRoomStopSalesVO, ContractHotelRateVO, ContractHotelOffersVO, ContractHotelSupplementVO, ContractHotelVO
from api_client import TravelCompositorAPI
from geocoding_client import geocode
from date_format import to_iso_date

# LAST LINE OF DEFENCE for the DD/MM/YYYY house rule. Screens convert on the way in and out
# (see date_format.py), but this module is what actually builds the payload, and Travel
# Compositor's LocalDate fields accept ONLY YYYY-MM-DD. Normalising here means a date that
# somehow reached the builder still displayed - a screen added later that forgot to convert,
# an older saved draft, a value pasted straight in - cannot become a rejected publish or, far
# worse, a season silently shifted by a month.
import transport_matcher
import hotel_matcher

DEFAULT_MEETING_POINT = ("Meet your guide in the airport arrival hall or, if you are already in the "
                          "tour's starting city, in your hotel lobby.")

# CONFIRMED REAL SYSTEM LIMIT (product owner): "we have the max of 9 People available, so when
# a price is seen for 10 or more pax, we can ignore that - for all services." Originally
# confirmed for Transfer only, now confirmed to apply universally - any occupancy/passenger
# bracket above this is genuinely unbookable in Travel Compositor regardless of product type,
# so it's dropped rather than sent. Shared by every product builder that deals in per-occupancy
# pricing tiers (Transfer today, Transport once built).
_MAX_OCCUPANCY_PAX = 9


def _extend_tiers_for_multi_vehicle_pricing(tiers_sorted, price_by_pax, max_cap=_MAX_OCCUPANCY_PAX):
    """
    CONFIRMED REAL RULE (product owner): "as 7-8 pax will be needed all the time in the
    Transport, we must check the prices for that transfer too. For example a Transport for 4
    Pax costs 100 Euro, so 8 People would pay 200 Euro, as in the worst case we must book 2
    transports." - confirmed this applies generally to Transfer too, not just Transport.

    Only applies to per-SERVICE/per-VEHICLE pricing (price_by_pax=False) - a flat price for the
    whole vehicle up to its stated capacity. Per-pax pricing doesn't need this: every person
    already pays the same rate regardless of group size, so the existing basePrice-as-default
    mechanism already covers any occupancy the source document doesn't explicitly list.

    When the source's largest documented vehicle bracket doesn't reach the 9-pax system cap,
    synthesizes the missing brackets as booking multiple copies of that same vehicle: price for
    N pax = ceil(N / largest_documented_capacity) * largest_documented_bracket's price. Ensures
    every per-vehicle transfer/transport always has full pricing coverage up to 9 pax, even when
    the supplier's rate sheet only ever describes a single (smaller) vehicle. Child/infant
    prices are scaled by the same vehicle-count multiplier, only when the source bracket itself
    priced them (never invents a child/infant price the source never gave).
    """
    if price_by_pax or not tiers_sorted:
        return tiers_sorted
    largest = tiers_sorted[-1]
    largest_occ = _safe_int(largest.get("occupancy", 1), fallback=1)
    largest_price = _safe_float(largest.get("price", 0))
    if largest_occ <= 0 or largest_occ >= max_cap:
        return tiers_sorted
    extended = list(tiers_sorted)
    for occ in range(largest_occ + 1, max_cap + 1):
        vehicles_needed = math.ceil(occ / largest_occ)
        synthesized = {"occupancy": occ, "price": round(vehicles_needed * largest_price, 2)}
        if largest.get("child_price") is not None:
            synthesized["child_price"] = round(vehicles_needed * _safe_float(largest.get("child_price")), 2)
        if largest.get("infant_price") is not None:
            synthesized["infant_price"] = round(vehicles_needed * _safe_float(largest.get("infant_price")), 2)
        extended.append(synthesized)
    return extended


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


_MONEY_KEYS = ("singlePrice", "doublePrice", "triplePrice", "quadruplePrice")


def _money_or_none(value, currency):
    """A MoneyVO-shaped dict, or None when the occupancy simply isn't sold.

    CONFIRMED REAL CRASH (product owner, ASW-6): "12 validation errors ...
    priceList.0.price.triplePrice.amount Field required [input_value={}]". The schema types
    these as Optional[MoneyVO], which permits None but NOT an empty object - and an empty
    object is exactly what a blank Triple/Quadruple column produces. So a perfectly ordinary
    two-occupancy tour could not be published at all, and the error named a pydantic path
    rather than the empty column that caused it.

    ABSENT AND ZERO ARE DIFFERENT and must stay that way: {} means this tour does not sell
    triple occupancy, and None is how the API says so. {"amount": 0} means it IS sold, at no
    extra charge, and dropping that to None would silently stop it being sellable."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {"amount": float(value), "currency": currency}
    if not isinstance(value, dict):
        return None
    if not value:
        return None
    amount = value.get("amount")
    if amount in (None, ""):
        return None
    # FOUND WHILE WRITING TESTS (2026-08-12): this used a bare float(amount) with no NaN/
    # Infinity guard, unlike every other numeric coercion in this file - the exact same bug
    # class as _safe_float's own docstring describes (NaN is truthy, so nothing here would
    # have caught it; requests' json= serialization rejects NaN outright at publish time,
    # producing "Out of range float values are not JSON compliant: nan" far from this
    # function). Routed through _safe_float so a NaN/Infinity amount is treated as absent
    # (the occupancy is dropped) rather than reaching the payload.
    amount = _safe_float(amount, fallback=None)
    if amount is None:
        return None
    return {"amount": amount, "currency": (value.get("currency") or currency or "EUR")}


def normalize_price_list(rows, currency):
    """Make a price list safe to validate, without changing what it says.

    Every occupancy that is priced keeps its number; every one that is blank becomes None
    rather than {}. Rows with no usable price at all are dropped, since a season row that
    prices nothing cannot be published and would only produce the same error later."""
    out = []
    for row in (rows or []):
        if not isinstance(row, dict):
            continue
        row = dict(row)
        # Dates leave ISO whatever came in - see the to_iso_date import note at the top.
        for date_key in ("startDate", "endDate"):
            if row.get(date_key):
                row[date_key] = to_iso_date(row[date_key])
        price = row.get("price")
        price = dict(price) if isinstance(price, dict) else {}
        cleaned = {}
        for key in _MONEY_KEYS:
            money = _money_or_none(price.get(key), currency)
            if money is not None:
                cleaned[key] = money
        for extra in ("tripleChildPercentageDiscount", "quadrupleChildPercentageDiscount"):
            if price.get(extra) not in (None, ""):
                try:
                    cleaned[extra] = float(price[extra])
                except (TypeError, ValueError):
                    pass
        if not any(k in cleaned for k in _MONEY_KEYS):
            continue
        row["price"] = cleaned
        out.append(row)
    return out


_TRANSFER_MAX_END_DATE = "2049-12-31"   # the house "runs indefinitely" date, as used for inventory


def normalize_supplement_time(value):
    """HH:MM for a supplement's time window, or None. Accepts '10pm', '22', '22:00', '22:00:00'."""
    text = str(value or "").strip().lower()
    if not text:
        return None
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?(?::\d{2})?\s*(am|pm)?$", text)
    if not m:
        return None
    hour, minute, meridiem = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    if hour == 24:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def build_transfer_supplement_vos(supplements, transfer_start_date="", transfer_end_date=""):
    """Mandatory transfer surcharges, as PERCENT or ABSOLUTE, scoped to a time window.

    CONFIRMED PRODUCT-OWNER RULES:
      - A transfer supplement is never optional. Optional extras (a child seat) are
        additionalServices, and this function is not where they go.
      - A percentage is sent AS a percentage. Travel Compositor applies "50%" to the base price
        itself, so the app sends amount=50 with type=PERCENT. Pre-calculating 50% into a currency
        figure would freeze the surcharge at whichever occupancy happened to be used for the
        arithmetic and mis-charge every other group size - that is the whole reason this is a
        percentage in the source document.
      - When the document states no dates for the surcharge, it inherits the TRANSFER's own
        validity window (falling back to today -> 2049-12-31), because a night surcharge is a
        property of the route for as long as the route is sold, not a separate season.
      - The time window may legitimately wrap past midnight (22:00 -> 08:00). That is stored as
        given; it is Travel Compositor's job to interpret it, and "fixing" it by splitting it into
        two windows would double the surcharge for anyone travelling across midnight."""
    out = []
    for s in (supplements or []):
        if not isinstance(s, dict):
            continue
        name = str(s.get("name") or "").strip()
        amount = _safe_float(s.get("amount", 0))
        raw_type = str(s.get("type") or "").strip().upper()
        is_percent = raw_type in ("PERCENT", "PERCENTAGE", "%") or bool(s.get("is_percentage"))
        if not name or amount == 0:
            continue
        out.append(TransferSupplementVO(
            name=name,
            type="PERCENT" if is_percent else "ABSOLUTE",
            amount=amount,
            startDate=(s.get("start_date") or transfer_start_date
                       or start_date_or_today(None)) or None,
            endDate=(s.get("end_date") or transfer_end_date or _TRANSFER_MAX_END_DATE) or None,
            startTime=normalize_supplement_time(s.get("start_time")),
            endTime=normalize_supplement_time(s.get("end_time")),
        ))
    return out


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


_DEFAULT_CANCELLATION_VOUCHER_TEXT = (
    "Free cancellation up to 30 days before arrival. Cancellation fees apply "
    "within 30 days of arrival or for no-shows."
)


def _cancellation_voucher_text(cancellation_policy_text, cancellation_tiers, default_text=_DEFAULT_CANCELLATION_VOUCHER_TEXT):
    """
    CONFIRMED REAL RULE (product owner): "For all services, we have to check the document
    or the URL, if the paper on the policy states something different, we have to include
    that to ALL services in the cancellation and in the Voucher remarks." - whatever
    cancellation policy actually applies (the source's own stated terms, OR our standing
    30-day/100%-refund default when the source states nothing) must ALWAYS be visible as
    plain text on the customer/staff-facing voucher, for every product type - not just
    reflected in the structured cancellationRanges/cancellation field.

    This used to be inconsistent across products (found while implementing this rule):
      - ClosedTour: voucherRemarks was hardcoded to "" always - the document's own stated
        cancellation policy was silently dropped from the voucher entirely, every time.
      - Ticket: voucherRemarks fell back to "" whenever nothing was extracted - the
        structured field correctly got the 30-day/100% default, but the voucher itself
        stayed blank about it, showing no cancellation info at all in the default case.
      - Transfer: went blank whenever the source HAD stated real tiers but the AI hadn't
        also produced a separate natural-language summary alongside them - so a genuinely
        different, document-stated policy could still silently vanish from the voucher.
    Single shared helper now used by every product builder (ClosedTour/Ticket/Transfer,
    and Transport once built) so this can't drift out of sync again.

    Priority: (1) the source's own natural-language summary, verbatim, if the AI extracted
    one - its own wording is more trustworthy than a synthesized rewrite; (2) if the source
    gave structured tiers but no separate summary text, synthesize one from the tiers so a
    real, document-stated policy is never silently dropped; (3) otherwise, the standing
    30-day/100%-refund default text, so the voucher is never blank about cancellation.
    """
    if cancellation_policy_text:
        return cancellation_policy_text
    if cancellation_tiers:
        lines = ["Cancellation Policy:"]
        for days, refund_pct in cancellation_tiers:
            fee_pct = round(100.0 - refund_pct, 2)
            if fee_pct <= 0:
                lines.append(f"- Free cancellation if cancelled at least {days} days before arrival.")
            elif days == 0:
                # The days=0 tier is the extraction convention for "day of check-in / no-show"
                # (see ai_extractor's cancellation_policy_tiers rule) - phrase it as such rather
                # than the slightly odd-sounding "within 0 days of arrival".
                if refund_pct <= 0:
                    lines.append("- No refund for cancellations on the day of arrival or no-shows.")
                else:
                    lines.append(f"- {fee_pct:g}% cancellation fee on the day of arrival or for no-shows "
                                  f"({refund_pct:g}% refund).")
            elif refund_pct <= 0:
                lines.append(f"- No refund if cancelled within {days} days of arrival.")
            else:
                lines.append(f"- {fee_pct:g}% cancellation fee if cancelled within {days} days of arrival "
                              f"({refund_pct:g}% refund).")
        return "\n".join(lines)
    return default_text


def _locked_on_update(existing_snapshot, field, fallback, label=""):
    """On an UPDATE, the LIVE record wins for fields that are properties of the service
    itself rather than of the document being read.

    CONFIRMED REAL RULE (product owner): "if updating a service it never has to be asked
    for code (it is set already), it never has to be asked for the currency (it also is
    set), it also never has to be asked for the min and max passenger."

    Those values were fixed when the service was created and are already live. A rate sheet
    arriving in July is a statement about PRICES, not about what currency the contract is
    denominated in - so letting the Step-2 dropdown (which defaults to EUR and is asked
    before anyone knows whether this is a create or an update) overwrite a live USD contract
    silently re-denominates every price on it. Same for occupancy limits: shrinking
    maxOccupancy on a live transfer invalidates bookings already taken against it.

    Returns (value, was_inherited) so a caller can say on screen where the value came from -
    an inherited value that looks like a chosen one is its own kind of trap."""
    if not existing_snapshot:
        return fallback, False
    current = existing_snapshot.get(field)
    if current in (None, "", []):
        return fallback, False
    return current, True


def _with_manual_notes(voucher_text, extracted_data):
    """Appends the human's manual notes to whatever voucher text was already built.

    Manual notes are things a person knows that the supplier's document doesn't say -
    "the pickup spot moved to the new terminal", "this supplier's cancellation terms
    changed in March". They arrive on the extracted-data dict under 'manual_notes',
    already composed by service_notes.compose_manual_notes() from the supplier-wide
    standing note plus anything typed for this one service.

    APPENDED, never substituted: a note is extra context, not a correction to what was
    extracted. Dropping the document's own cancellation policy because someone added a
    remark would be a silent, customer-visible data loss - so both always survive, with
    the note last since it's the more recent and more specific information.

    Applied through the extracted-data dict rather than a new function parameter so the
    five builders' signatures (and every caller of them) stay unchanged."""
    note = ((extracted_data or {}).get("manual_notes") or "").strip()
    if not note:
        return voucher_text
    return f"{voucher_text}\n\n{note}".strip() if voucher_text else note


_PRICE_COLUMN_ALIASES = {
    "singleprice": "singlePrice", "single": "singlePrice", "sgl": "singlePrice",
    "doubleprice": "doublePrice", "double": "doublePrice", "dbl": "doublePrice",
    "tripleprice": "triplePrice", "triple": "triplePrice", "tpl": "triplePrice",
    "quadrupleprice": "quadruplePrice", "quadruple": "quadruplePrice",
    "quad": "quadruplePrice", "qdp": "quadruplePrice",
}


def coerce_price_list_shape(rows, currency="EUR"):
    """Force a price list into the one shape the screens and the payload expect.

    CONFIRMED REAL CRASH (product owner, ClosedTour modality): "Tell AI what to fix" returned a
    price_list whose rows carried `price` as a bare number rather than the nested per-occupancy
    object, and the pricing table died on `price.get(...)` with an AttributeError - taking the
    whole screen down and pointing at a line of display code that was not at fault.

    THE REAL DEFECT WAS UPSTREAM: apply_clarification merges whatever the model returns straight
    into the working data, with nothing checking the shape. Any field can come back malformed;
    price_list is simply the one that gets rendered into a table immediately afterwards. So this
    runs at BOTH ends - when a clarification is merged, and again when the table is built - and
    it never raises, because a screen that cannot draw itself is worse than a row that needs
    fixing by hand.

    Returns (rows, notes). `notes` names anything that could not be read confidently, so the
    human is told rather than left with a quietly emptied row.
    """
    out, notes = [], []
    for index, row in enumerate(rows or []):
        label = f"row {index + 1}"
        if not isinstance(row, dict):
            notes.append(f"{label} was not a price row at all and was dropped")
            continue
        entry = {k: v for k, v in row.items() if k not in ("price",)}
        prices = {}

        raw = row.get("price")
        if isinstance(raw, dict):
            for key, value in raw.items():
                canon = _PRICE_COLUMN_ALIASES.get(str(key).strip().lower())
                if not canon:
                    continue
                if isinstance(value, dict):
                    amount = value.get("amount")
                    row_currency = value.get("currency") or currency
                else:
                    amount, row_currency = value, currency
                if amount in (None, ""):
                    continue
                try:
                    prices[canon] = {"amount": float(amount), "currency": row_currency}
                except (TypeError, ValueError):
                    notes.append(f"{label}'s {canon} was not a number and was left blank")
        elif raw not in (None, ""):
            # A bare number. It could mean per person, or the double rate, or a total - and
            # each reading bills a different amount. Refusing to guess: the dates survive so
            # the row stays visible and editable, and the note says exactly what to do.
            notes.append(f"{label} had a single unlabelled price ({raw}) with no occupancy - "
                         f"enter it under the right column")

        # A very common alternative shape: the occupancies sitting on the row itself.
        for key, value in row.items():
            canon = _PRICE_COLUMN_ALIASES.get(str(key).strip().lower())
            if not canon or canon in prices or value in (None, ""):
                continue
            if isinstance(value, dict):
                value = value.get("amount")
            try:
                prices[canon] = {"amount": float(value), "currency": currency}
                entry.pop(key, None)
            except (TypeError, ValueError):
                pass

        entry["price"] = prices
        entry["startDate"] = str(entry.get("startDate") or "")
        entry["endDate"] = str(entry.get("endDate") or "")
        out.append(entry)
    return out, notes


def sold_occupancies(price_list):
    """Which occupancies this tour actually sells, read from its own price list."""
    sold = set()
    for row in (price_list or []):
        price = row.get("price") if isinstance(row, dict) else None
        if not isinstance(price, dict):
            continue
        for key in _MONEY_KEYS:
            block = price.get(key)
            amount = block.get("amount") if isinstance(block, dict) else block
            if amount not in (None, ""):
                sold.add(key)
    return sold


_SUPPLEMENT_OCCUPANCY_FIELDS = {
    "singlePrice": "single_price", "doublePrice": "double_price",
    "triplePrice": "triple_price", "quadruplePrice": "quadruple_price",
}


def strip_unsold_supplement_occupancies(supplements, price_list):
    """Zero any supplement occupancy the tour does not actually sell.

    CONFIRMED HOUSE RULE (product owner): "If no price in Triple or in Quadruple, there can't be
    any price for triple or quadruple in the supplement neither - it goes hand in hand."

    Enforced in code rather than left to the extraction prompt, because it is a consistency rule
    BETWEEN two separate parts of the payload, and a model has no reliable way to hold both in
    view at once. The failure it prevents is quiet: a triple supplement on a tour with no triple
    rate is not rejected by the API - it sits there attached to an occupancy that was never sold.

    Returns (supplements, notes). The notes name every amount removed, so nothing disappears
    without the human being told which supplement and which occupancy."""
    sold = sold_occupancies(price_list)
    if not sold:
        # No prices at all yet: there is nothing to be consistent WITH, and stripping here would
        # empty every supplement on a half-filled screen.
        return list(supplements or []), []

    out, notes = [], []
    for supplement in (supplements or []):
        if not isinstance(supplement, dict):
            continue
        row = dict(supplement)
        for money_key, field in _SUPPLEMENT_OCCUPANCY_FIELDS.items():
            if money_key in sold:
                continue
            value = row.get(field)
            try:
                has_value = value not in (None, "") and float(value) != 0
            except (TypeError, ValueError):
                has_value = False
            if has_value:
                occupancy = field.replace("_price", "")
                notes.append(f"'{row.get('name') or 'unnamed supplement'}' had a {occupancy} "
                             f"amount ({value}), but this tour sells no {occupancy} rate — removed")
            row[field] = 0
        out.append(row)
    return out, notes


def per_night_occupancy_prices(nights, per_night_rate):
    """Single and double totals from a per-night rate.

    CONFIRMED HOUSE RULE (product owner): "for ClosedTours Nile Cruises, the prices are generally
    per night. Single price = Number of ClosedTour nights x Cost per night. Double price =
    (Number of ClosedTour nights x Cost per night)/2 (as two people share the costs)."

    The halving is not a discount: the quoted rate buys the cabin, and two people sharing it each
    pay half of it. Triple and quadruple are deliberately NOT derived the same way - an occupancy
    that the document does not price is not sold, and inventing it by dividing by three would
    create a bookable rate nobody agreed to."""
    try:
        nights = int(nights)
        rate = float(per_night_rate)
    except (TypeError, ValueError):
        return None, None
    if nights <= 0 or rate <= 0:
        return None, None
    single = round(nights * rate, 2)
    return single, round(single / 2, 2)


def resolve_child_age_band(stated_min, stated_max, default_min=2, default_max=12):
    """The child age band to publish, given whatever the document stated.

    CONFIRMED HOUSE RULE (product owner): "When the document says minimum child age (e.g. 7),
    then this must be added to the child age allowed. The range for children would be then
    7 to 12." So a stated MINIMUM replaces the house floor; the ceiling stays at the house
    maximum unless the document names a different one.

    A MISSING ceiling never becomes the minimum: "children from 7" with nothing said about an
    upper age gives 7-12, not 7-7. A zero-width band would bill every 7-to-12-year-old as an
    infant, and the payload would validate perfectly while doing it.

    An INVERTED band (min above max) is repaired rather than published, by lifting the ceiling
    to meet the minimum - the only reading of "children from 14" that still sells anything.

    What this deliberately does NOT do is second-guess a ceiling the document actually stated.
    If both ends come back as 7, that is left alone and flagged on the review screen instead:
    it might be a model error, or the source might really say it, and only a human looking at
    the document can tell.

    Note what a raised minimum MEANS downstream: below it, a traveller is an infant, not a
    rejected booking. Travel Compositor has no "under-N not accepted" field on this record, so
    a genuine refusal has to be written into the description - see the extraction prompt."""
    def _age(value, fallback):
        if value is None or value == "":
            return fallback
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return fallback

    low = _age(stated_min, default_min)
    high = _age(stated_max, default_max)
    # A stated minimum must not drag the ceiling down with it (failure mode 1).
    if high < low:
        high = max(low, _age(default_max, 12))
    return low, high


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

        # CONFIRMED PRODUCT-OWNER CORRECTION: "Supplement within ClosedTour is set only once and
        # applies to ALL Modalities to the ClosedTour." An empty modalityCodes is exactly how
        # Travel Compositor spells that, so it is now ALWAYS empty.
        #
        # THIS REVERSES AN EARLIER FIX OF MINE, and the earlier reasoning was wrong. Seeing one
        # supplement listed against three room categories, I read it as triple-charging and
        # scoped each supplement to a single Modality. But a ClosedTour supplement is a property
        # of the TOUR - one optional Abu Simbel excursion, offered whichever cabin you booked -
        # so scoping it to one Modality meant a client in any other cabin could not buy it at
        # all. Nothing would have looked wrong on screen; the excursion would simply never be
        # offered. Any `applies_to` still arriving from an older draft is ignored.
        supplements_list.append(SupplementVO(
            translations={"EN": SupplementTranslation(name=s.get("name", ""))},
            price=SupplementPriceVO(singlePrice=single_val, doublePrice=double_val,
                                   triplePrice=triple_val, quadruplePrice=quadruple_val),
            modalityCodes=[],   # empty = every Modality on this tour
            mandatory=bool(s.get("mandatory", False)),
            onRequest=bool(s.get("on_request", False)),
            # HOUSE RULE (product owner, confirmed): a ClosedTour supplement is NEVER
            # refundable. The schema defaults this to True, so leaving it unset was quietly
            # publishing every optional excursion and upgrade as refundable - the opposite of
            # the commercial terms. Set explicitly rather than relying on any default.
            refundable=False,
            free=(price_val == 0),
            travelWindows=travel_windows,
        ))
    return supplements_list


def build_ticket_supplement_vos(supplements: List[Dict[str, Any]]) -> List[TicketSupplementVO]:
    """
    Converts the app's internal flat Ticket-Modality supplement dicts (name/
    adult_price_supplement/children_price_supplement/infant_price_supplement/
    start_date/end_date) into the real TicketSupplementVO wire shape.

    CORRECTED 2026-08-12 (product owner): an earlier version of this codebase
    treated Tickets as having no supplements at all - wrong. The main Ticket
    record has none, but ContractTicketModalityVO DOES carry its own
    `supplements: List[TicketSupplementVO]`, confirmed against the real
    schema. This is the mechanism for a DATED price change that is not a
    customer choice - a seasonal price table where the same excursion costs
    more in high season, or a holiday/peak-date surcharge (e.g. a Tet Holiday
    guide-language surcharge) - as opposed to a genuinely different product
    variant (a foreign-language guide, a Seat-in-Coach option), which still
    becomes its own Modality via the existing extra-costs mechanism, unchanged.

    Every entry requires startDate/endDate (TicketSupplementVO has no
    optional-date fallback like ClosedTour's SupplementVO) - an entry with
    either date missing is skipped rather than published with an empty/
    always-on date, since an undated Ticket supplement can't be told apart
    from a permanent price increase.
    """
    supplements_list = []
    for s in (supplements or []):
        start = (s.get("start_date") or "").strip()
        end = (s.get("end_date") or "").strip()
        if not start or not end:
            continue
        supplements_list.append(TicketSupplementVO(
            adultPriceSupplement=_safe_float(s.get("adult_price_supplement", 0)),
            childrenPriceSupplement=_safe_float(s.get("children_price_supplement", 0)),
            infantPriceSupplement=_safe_float(s.get("infant_price_supplement", 0)),
            startDate=start,
            endDate=end,
            translations={"EN": TicketSupplementTranslation(name=s.get("name") or "Seasonal surcharge")},
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
        # HOUSE RULE, enforced here rather than trusted to the extraction: an occupancy this
        # tour does not sell cannot carry a supplement - see
        # strip_unsold_supplement_occupancies. Applied against the SAME price list that is about
        # to be published, so the two can never disagree.
        _consistent_supplements, _supplement_notes = strip_unsold_supplement_occupancies(
            extracted_dmc_data.get("supplements", []),
            normalize_price_list(extracted_dmc_data.get("price_list", []), pre_config.currency))
        supplements_list = build_supplement_vos(_consistent_supplements)

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

        # CONFIRMED REAL RULE (product owner): voucherRemarks used to be hardcoded blank
        # here always, silently dropping the document's own stated cancellation policy from
        # the voucher entirely - see _cancellation_voucher_text()'s docstring for the full
        # rule and the cross-product inconsistency this fixes.
        datasheet_en = DatasheetEN(
            name=extracted_dmc_data.get("tour_name") or "",
            description=extracted_dmc_data.get("description") or "",
            hotels=extracted_dmc_data.get("hotels_text") or "",
            voucherRemarks=_with_manual_notes(
                _cancellation_voucher_text(extracted_dmc_data.get("cancellation_policy_text"), cancellation_tiers),
                extracted_dmc_data),
            included=extracted_dmc_data.get("included") or "",
            excluded=extracted_dmc_data.get("excluded") or "",
            meetingPoint=extracted_dmc_data.get("meeting_point") or DEFAULT_MEETING_POINT,
            remarksTitle="Policy",
            remarksDescription=extracted_dmc_data.get("policy_remarks") or ""
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
            # CONFIRMED HOUSE RULE (product owner): a minimum child age stated in the document
            # replaces the house floor of 2, and the ceiling stays 12 - "children from 7 years"
            # gives a 7-12 band. See resolve_child_age_band for why this is not just a passthrough.
            **dict(zip(("minChildAge", "maxChildAge"), resolve_child_age_band(
                extracted_dmc_data.get("min_child_age"), extracted_dmc_data.get("max_child_age"),
                pre_config.min_child_age, pre_config.max_child_age))),
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
            priceList=sorted(
                normalize_price_list(extracted_dmc_data.get("price_list", []), pre_config.currency),
                key=lambda p: p.get("startDate", "")),
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
        # Occupancies removed for consistency with the price list - named, never silent.
        "supplement_occupancy_notes": _supplement_notes,
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

        # CORRECTED 2026-08-12 (product owner): "Main Ticket information has no supplement, Modality
        # of a Ticket has their own supplement." An earlier version of this code treated Tickets as
        # having NO supplements at all, which was too broad - the main Ticket record has none, but
        # each Modality (ContractTicketModalityVO) has its own dated supplements list, confirmed
        # against the real schema. A genuinely different product variant (a foreign-language guide,
        # a Seat-in-Coach option) still becomes its own Modality via
        # build_ticket_modality_combinations() - that part is unchanged. This field is for a DATED
        # price change that is not a customer choice - a seasonal price table or a holiday surcharge -
        # see build_ticket_supplement_vos()'s docstring.
        supplements_list = build_ticket_supplement_vos(extracted_ticket_data.get("modality_supplements"))

        # The OLD "supplements" key is legacy - anything still sitting there (an older saved draft,
        # or a model that ignored the prompt and used the wrong field) is deliberately DROPPED rather
        # than published, since its shape (no dates, per-passenger amount only) was never real.
        _ignored_ticket_supplements = [
            str((s or {}).get("name") or "").strip()
            for s in (extracted_ticket_data.get("supplements") or []) if isinstance(s, dict)
        ]

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
        # CONFIRMED REAL RULE (product owner): the cancellation policy that actually applies
        # (document-stated, or our standing default) must always reach the voucher - see
        # _cancellation_voucher_text()'s docstring. `voucher_remarks` (a broader, human-
        # editable field, not cancellation-specific) still wins if a human explicitly set it.
        ticket_cancellation_voucher_text = _with_manual_notes(
            _cancellation_voucher_text(
                extracted_ticket_data.get("cancellation_policy_text"), ticket_cancellation_tiers
            ),
            extracted_ticket_data,
        )

        datasheet_en = TicketDatasheetEN(
            name=extracted_ticket_data.get("ticket_name") or "",
            description=extracted_ticket_data.get("description") or "",
            meetingPoint=extracted_ticket_data.get("meeting_point_summary") or "Hotel Lobby",
            departureTime=time_tables_list[0] if time_tables_list else "",
            voucherRemarks=extracted_ticket_data.get("voucher_remarks") or ticket_cancellation_voucher_text,
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
        #
        # CONFIRMED REAL SYSTEM LIMIT (product owner, same rule as _MAX_OCCUPANCY_PAX above -
        # "we have the max of 9 People available, so when a price is seen for 10 or more pax,
        # we can ignore that - for all services"): app.py already drops occupancy rows above
        # this cap in its own UI, but that only covers the interactive path. Enforcing it again
        # here means a stale saved draft, an "update_option" pre-fill straight from Travel
        # Compositor's live data, or any future caller of this function can never publish an
        # unbookable 10+ pax tier even if the UI-level filter is ever bypassed or forgotten.
        #
        # CONFIRMED REAL BUG (production failure, real API response): "Number of passengers
        # in occupancy is greater than max passengers allowed in the contract" - the real
        # ceiling is THIS TICKET'S OWN maxPassengers (a human-set value that can be as low as
        # 2), not the flat system-wide 9. 9 is only the upper bound of the upper bound. Cap
        # against whichever is actually lower so a ticket configured for e.g. max 6 passengers
        # can never carry a 7-9 pax occupancy row that Travel Compositor would reject outright.
        effective_occupancy_cap = min(_MAX_OCCUPANCY_PAX, _safe_int(pre_config.max_passengers, fallback=_MAX_OCCUPANCY_PAX))
        occupancy_prices = [
            {"occupancy": _safe_int(o.get("occupancy", 1), fallback=1), "amount": _safe_float(o.get("amount", 0))}
            for o in (extracted_ticket_data.get("occupancy_prices") or []) if isinstance(o, dict)
            and _safe_int(o.get("occupancy", 1), fallback=1) <= effective_occupancy_cap
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
            # blank - now always carries the SAME cancellation text shown on
            # the Voucher Remarks field above (via _cancellation_voucher_text -
            # document-stated policy, or the standing default when nothing was
            # stated), so staff see it too and the two fields can't drift apart.
            remarks={"EN": TicketRemark(name=pre_config.modality_code, remarks=ticket_cancellation_voucher_text)},
            supplements=supplements_list,
            stopSales=combined_ticket_stop_sales,
            ticketsPerDay=99,
            disallowChildren=bool(extracted_ticket_data.get("disallow_children", False)),
            onRequest=pre_config.on_request,
            disallowInfant=bool(extracted_ticket_data.get("disallow_infant", False)),
            disallowAdult=bool(extracted_ticket_data.get("disallow_adult", False)),
            startDate=start_date_or_today(extracted_ticket_data.get("start_date")),
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
            # internationally standard, same for Tickets and ClosedTours.
            #
            # CONFIRMED REAL BUG: this used to be a bare "if not None else default" passthrough,
            # skipping resolve_child_age_band's inversion repair that ClosedTour already gets
            # (see line ~1060 above). A document saying "children accepted from age 14" with no
            # stated ceiling extracts as child_age_min=14, child_age_max=None - the passthrough
            # published childAgeMin=14, childAgeMax=12, an INVERTED band that (per
            # resolve_child_age_band's own docstring) bills every child as an infant, for the
            # exact same kind of source statement ClosedTour already handles correctly.
            **dict(zip(("childAgeMin", "childAgeMax"), resolve_child_age_band(
                extracted_ticket_data.get("child_age_min"), extracted_ticket_data.get("child_age_max"),
                2, 12))),
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
        # Named out loud rather than dropped in silence - see the supplements block above.
        "ignored_ticket_supplements": [n for n in _ignored_ticket_supplements if n],
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
        # structured cancellation field, unlike ClosedTour/Ticket. See
        # _cancellation_voucher_text()'s docstring: this used to go BLANK whenever the
        # source had real tiers but no separate summary sentence - a genuinely
        # document-stated policy could silently vanish from the voucher. Now synthesizes
        # text from the tiers themselves in that case, instead of dropping it.
        cancellation_tiers = _cancellation_ranges_from_tiers(extracted_transfer_data.get("cancellation_policy_tiers"))
        voucher_text = _with_manual_notes(
            _cancellation_voucher_text(extracted_transfer_data.get("cancellation_policy_text"), cancellation_tiers),
            extracted_transfer_data)
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
        # An existing transfer's own currency is authoritative - see _locked_on_update.
        currency, _currency_inherited = _locked_on_update(
            existing_transfer_snapshot, "currency", currency)

        # CONFIRMED REAL SYSTEM LIMIT (product owner): TC caps bookings at 9 passengers - a
        # supplier rate sheet pricing larger vehicles (e.g. a 9-14 pax coach tier) is pricing
        # something TC can never actually book, so those tiers are dropped here rather than
        # sent, and never counted toward max_occupancy below.
        tiers = [
            t for t in (extracted_transfer_data.get("occupancy_price_tiers") or [])
            if isinstance(t, dict) and _safe_int(t.get("occupancy", 1), fallback=1) <= _MAX_OCCUPANCY_PAX
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

        # CONFIRMED REAL RULE (product owner, same rule _locked_on_update's docstring already
        # states): "it also never has to be asked for the min and max passenger" on an UPDATE -
        # those were fixed when the service was created. This used to be computed purely from
        # extracted_transfer_data on every call, update included, so a partial/simplified rate
        # sheet (e.g. one that only prices up to 4 pax) could silently SHRINK a live transfer's
        # maxOccupancy below what real bookings already taken against it require - exactly the
        # failure _locked_on_update's own docstring warns about ("shrinking maxOccupancy on a
        # live transfer invalidates bookings already taken against it").
        min_occupancy, _min_occ_inherited = _locked_on_update(
            existing_transfer_snapshot, "minOccupancy",
            _safe_int(extracted_transfer_data.get("min_occupancy", 1), fallback=1) or 1)
        max_occupancy_fallback = min(
            _safe_int(extracted_transfer_data.get("max_occupancy", 4), fallback=4) or 1,
            _MAX_OCCUPANCY_PAX,
        )
        max_occupancy, _max_occ_inherited = _locked_on_update(
            existing_transfer_snapshot, "maxOccupancy", max_occupancy_fallback)

        # CONFIRMED REAL RULE (product owner): a per-vehicle rate sheet that only describes a
        # single (smaller) vehicle must still price every occupancy up to the 9-pax system cap -
        # see _extend_tiers_for_multi_vehicle_pricing()'s docstring. Extends tiers_sorted BEFORE
        # max_occupancy is capped below, since this genuinely extends real bookable coverage up
        # to 9 pax (via multiple vehicles), not just a display default.
        tiers_sorted = _extend_tiers_for_multi_vehicle_pricing(tiers_sorted, price_by_pax)
        if not price_by_pax and tiers_sorted:
            # Multi-vehicle synthesis means this route is now genuinely bookable up to the full
            # system cap, even though the source document's own stated max_occupancy was for a
            # single vehicle only.
            max_occupancy = max(max_occupancy, _safe_int(tiers_sorted[-1].get("occupancy", 1), fallback=1))

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
        # handling above for why a location-conditional fee never ends up here. Built after the
        # effective date window is known, further down, so a supplement with no stated dates can
        # inherit the transfer's own validity rather than being left open-ended.
        raw_transfer_supplements = [
            s for s in (extracted_transfer_data.get("mandatory_supplements") or []) if isinstance(s, dict)
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
            effective_start_date = start_date_or_today(extracted_transfer_data.get("start_date"))
            effective_end_date = extracted_transfer_data.get("end_date") or ""
            effective_images = []
            effective_properties = [p.dict() for p in _DEFAULT_TRANSFER_PROPERTIES]

        supplements = build_transfer_supplement_vos(
            raw_transfer_supplements, effective_start_date, effective_end_date)

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


# ==========================================
# TRANSPORT
# Confirmed field-by-field against the real Transport Swagger + real GET
# examples across 2 suppliers/routes (Aswan-Hurghada CAR, Praslin-La Digue
# COMBINED car+ferry+car). See schemas.py's ContractTransportVO/
# ContractTransportOptionVO docstrings for the confirmed shape, and
# transport_matcher.py for how an existing transport/option is recognized
# for an update.
# ==========================================

# CONFIRMED REAL RULE (product owner): "Transport Type would be either: Car (for private),
# Van (for shuttle) or Combined (if Transfer + Train or Transfer and Boat, e.g. island to
# island)." The SERVICE CLASS decides it, not the vehicle words in the text - a shuttle is a
# VAN even when the row also says "car". Ordered most-specific first, and the private/shuttle
# rules sit above the generic vehicle keywords so they cannot be overridden by them.
_TRANSPORT_TYPE_KEYWORDS = [
    ("COMBINED", ["combined", "car and ferry", "car + ferry", "multi-leg", "multi leg",
                  "and ferry", "and boat", "and train", "+ train", "+ boat", "+ ferry",
                  "island to island", "island-to-island"]),
    ("PLANE", ["flight", "plane", "airline", "airplane", "aircraft"]),
    ("VAN", ["shuttle", "seat in coach", "seat-in-coach", "sic ", "shared"]),
    ("CAR", ["private", "limousine", "individual"]),
    ("TRAIN", ["train", "rail"]),
    ("FERRY", ["ferry", "boat"]),
    ("BUS", ["bus", "coach"]),
    ("VAN", ["van", "minivan", "minibus"]),
    ("CAR", ["car", "sedan"]),
]


def start_date_or_today(stated):
    """CONFIRMED REAL RULE (product owner): "start date of the Transfer, Transport, Ticket can
    always be the day on the document, and if not stated, it is today."

    Filled here rather than by the AI so it cannot be invented: a hallucinated start date in
    the future makes a product silently unbookable until it arrives, and one in the past is
    equally invisible in a different way. Today is a fact this code has."""
    stated = to_iso_date((stated or "").strip())
    return stated or datetime.date.today().isoformat()


def round_duration_up_to_hour(duration_time):
    """Round a journey duration UP to the next whole hour.

    CONFIRMED REAL RULE (product owner): "the time shall be rounded up to full hour." Up, never
    nearest: a transport that claims to arrive earlier than it can is a customer standing at a
    meeting point that is not there yet. An exact whole hour is left alone."""
    normalized = normalize_time_hhmmss(duration_time or "")
    if not normalized:
        return ""
    try:
        hh, mm, ss = (int(x) for x in normalized.split(":"))
    except (ValueError, AttributeError):
        return normalized
    if mm or ss:
        hh += 1
    return f"{hh:02d}:00:00"


def transport_description(service_name, departure_name, arrival_name):
    """The house one-sentence description.

    CONFIRMED REAL TEMPLATE (product owner): "[Transport style like Private, or Shuttle].
    Transfer between [ORIGIN] and your booked accommodation in [DESTINATION]." Built here
    rather than asked of the AI: it is a fixed sentence with two names in it, and a model
    writing it freshly each time produces forty slightly different sentences across one
    rate sheet."""
    style = (service_name or "").strip()
    # "Tranfer" (sic) is how the real rate sheet spells it, so the pattern tolerates the
    # missing s rather than leaving the word in the middle of the sentence.
    style = re.sub(r"(?i)\btra?ns?fers?\b", "", style)
    style = re.sub(r"(?i)\btransports?\b", "", style)
    style = re.sub(r"\s{2,}", " ", style).strip(" -–—,")
    style = style or "Transfer"
    origin = (departure_name or "").strip() or "the pick-up point"
    destination = (arrival_name or "").strip() or "your destination"
    return (f"{style}. Transfer between {origin} and your booked accommodation "
            f"in {destination}.")


def transport_company_name(service_name):
    """CONFIRMED REAL TEMPLATE (product owner): "Company name would be 'Private from Hotel to
    Hotel'." Built from the service class so a shuttle reads "Shuttle from Hotel to Hotel"
    without anyone typing it forty times."""
    style = (service_name or "").strip()
    style = re.sub(r"(?i)\btra?ns?fers?\b", "", style)
    style = re.sub(r"(?i)\btransports?\b", "", style)
    style = re.sub(r"\s{2,}", " ", style).strip(" -–—,") or "Private"
    return f"{style} from Hotel to Hotel"


def transport_display_name(departure_name, arrival_name):
    """CONFIRMED REAL TEMPLATE (product owner): name is "DEPARTURE - ARRIVAL"."""
    dep = (departure_name or "").strip()
    arr = (arrival_name or "").strip()
    if dep and arr:
        return f"{dep} - {arr}"
    return dep or arr or ""


def _map_transport_type(type_hint: str, service_name: str) -> str:
    """Best-effort mapping - only CAR, COMBINED (both real live examples), and PLANE (the
    Swagger's own placeholder example value) are confirmed; the rest of the Swagger's stated
    8-value transportType enum is unconfirmed. Reviewable/editable per record, same convention
    as Transfer's vehicleType mapping (_map_transfer_vehicle_type)."""
    text = f"{type_hint or ''} {service_name or ''}".lower()
    for enum_val, keywords in _TRANSPORT_TYPE_KEYWORDS:
        if any(k in text for k in keywords):
            return enum_val
    return "CAR"


def derive_arrival_from_duration(departure_time, duration_time):
    """(arrival_time, plus_days) from a departure and a journey duration.

    CONFIRMED REAL RULE (product owner): "we must add in Transport a Duration Time... The human
    shall in best case only select Departure time." So duration is the fact about the route,
    departure is the operator's choice, and arrival is arithmetic - the one of the three nobody
    should be typing. A hand-typed arrival is also the one most likely to be wrong: the whole
    reason this came up is that both times defaulted to 09:00, publishing a five-hour drive as
    taking no time at all.

    plus_days is set when the arithmetic crosses midnight, because an overnight journey that
    reports arriving before it departed is worse than one with no time at all."""
    dep = normalize_time_hhmmss(departure_time or "")
    dur = normalize_time_hhmmss(duration_time or "")
    if not dep or not dur:
        return None, 0
    try:
        dh, dm, ds = (int(x) for x in dep.split(":"))
        uh, um, us = (int(x) for x in dur.split(":"))
    except (ValueError, AttributeError):
        return None, 0
    total = (dh * 3600 + dm * 60 + ds) + (uh * 3600 + um * 60 + us)
    plus_days, remainder = divmod(total, 24 * 3600)
    hh, rest = divmod(remainder, 3600)
    mm, ss = divmod(rest, 60)
    return f"{hh:02d}:{mm:02d}:{ss:02d}", int(plus_days)


def _add_minimum_charge_bracket(brackets_sorted, price_per_pax, min_billable_pax=None,
                                max_cap=_MAX_OCCUPANCY_PAX):
    """Make a solo traveller sellable on a per-person rate that has a minimum party size.

    CONFIRMED REAL RULE (product owner): "Private Transfer p.p. valid for (Min.2 pax) in
    Vehicle" means "1 Pax must get an own Modality for the Transport and gets a surcharge, so
    that one pax pays the price as for 2 pax."

    Why it has to be synthesised rather than left to the document: the rate sheet prices ONE
    thing, a per-person rate valid from two people up. Travel Compositor sells per occupancy
    bracket, so if the lowest bracket starts at 2 the product simply cannot be booked by one
    person - it silently disappears from search for every solo enquiry, which is lost revenue
    nobody sees. Publishing the per-person rate down to 1 instead would be worse: it would
    sell a private vehicle at half what the supplier charges, and the loss is real money.

    So a 1..(minimum-1) bracket is added, priced at the per-person rate TIMES the minimum -
    one person pays the two-person total. This is not a guess: a real Aswan-Hurghada transport
    already in Travel Compositor carries exactly this shape, a narrow 1-pax bracket at 180
    against a 2-9 pax bracket at 90 per person.

    Only ever applies to PER-PERSON pricing. A per-vehicle rate has no such problem - the
    vehicle costs the same whoever is in it - and doubling it there would overcharge."""
    if not price_per_pax or not brackets_sorted:
        return brackets_sorted
    lowest = brackets_sorted[0]
    lowest_min = _safe_int(lowest.get("min_occupancy", 1), fallback=1)
    minimum = _safe_int(min_billable_pax, fallback=0) or lowest_min
    # Nothing to do when one person can already book, and never invent a bracket above the cap.
    if minimum <= 1 or lowest_min <= 1 or minimum > max_cap:
        return brackets_sorted
    unit_price = _safe_float(lowest.get("price", 0))
    if unit_price <= 0:
        return brackets_sorted
    solo = {
        "min_occupancy": 1,
        "max_occupancy": max(1, minimum - 1),
        # Per person, and this bracket holds one person - so the per-person figure IS the
        # minimum-party total.
        "price": round(unit_price * minimum, 2),
        "child_price": (round(_safe_float(lowest.get("child_price")) * minimum, 2)
                        if lowest.get("child_price") is not None else None),
        "infant_price": (round(_safe_float(lowest.get("infant_price")) * minimum, 2)
                         if lowest.get("infant_price") is not None else None),
        "synthesized_minimum_charge": True,
    }
    return [solo] + list(brackets_sorted)


def _extend_transport_brackets_for_multi_vehicle_pricing(brackets_sorted, price_per_pax, max_cap=_MAX_OCCUPANCY_PAX):
    """
    Transport-specific counterpart to _extend_tiers_for_multi_vehicle_pricing (see that
    function's docstring for the full confirmed rule, product owner: "as 7-8 pax will be needed
    all the time... we must check the prices for that transfer too... in the worst case we must
    book 2 transports" - confirmed to apply generally, Transport included). Operates on RANGE
    brackets (min_occupancy/max_occupancy), not single-occupancy tiers, since Transport's
    per-occupancy pricing is modelled as separate Option sub-resources. Synthesized coverage is
    grouped into contiguous multi-vehicle brackets (e.g. capacity=4 pax -> one bracket covering
    5-8 pax at 2x the price, not four separate single-pax brackets), matching how real
    per-vehicle-style options are actually structured (every real bracket example seen spans a
    range, never a single pax count).
    """
    if price_per_pax or not brackets_sorted:
        return brackets_sorted
    largest = brackets_sorted[-1]
    capacity = _safe_int(largest.get("max_occupancy", 0), fallback=0)
    price = _safe_float(largest.get("price", 0))
    if capacity <= 0 or capacity >= max_cap:
        return brackets_sorted
    extended = list(brackets_sorted)
    occ = capacity + 1
    while occ <= max_cap:
        vehicles_needed = math.ceil(occ / capacity)
        bracket_max = min(max_cap, vehicles_needed * capacity)
        extended.append({
            "min_occupancy": occ,
            "max_occupancy": bracket_max,
            "price": round(vehicles_needed * price, 2),
            "child_price": None,
            "infant_price": None,
        })
        occ = bracket_max + 1
    return extended


# Places whose short code is not simply the first three letters. The generic rule below
# ("first three letters, uppercase") gets Luxor -> LUX and Aswan -> ASW right on its own;
# these are the ones where the operator's own codes use the airport/IATA form instead.
_PLACE_SHORT_CODE = {
    "hurghada": "HRG", "marsa alam": "RMF", "sharm el sheikh": "SSH", "sharm": "SSH",
    "cairo": "CAI", "el gouna": "EGN", "elgouna": "EGN", "makadi bay": "MKD",
    "soma bay": "SOM", "sahl hashish": "SHH", "safaga": "SFG", "el quseir": "QSR",
    "port ghalib": "PTG", "abu simbel": "ABS", "alexandria": "HBE", "taba": "TCP",
    "dahab": "DHB", "nuweiba": "NUW", "ain sokhna": "SOK",
}


_TICKET_MAX_MODALITIES = 24
_TICKET_EXTRA_TOKEN_LEN = 4


def _ticket_extra_token(name):
    """A short uppercase token for a modality code - no '-', '+', '/' or '\\'.

    CONFIRMED API CONSTRAINT: those characters break Travel Compositor's URL lookups and the
    publish is rejected, so option tokens are simply run together (the same convention the live
    transport options LUXHRG1 / LUXHRG29 use)."""
    words = re.findall(r"[A-Za-z0-9]+", str(name or ""))
    if not words:
        return "X"
    if len(words) == 1:
        return words[0][:_TICKET_EXTRA_TOKEN_LEN].upper()
    return "".join(w[0] for w in words)[:_TICKET_EXTRA_TOKEN_LEN].upper()


def build_ticket_modality_combinations(base_prices, extra_options, base_code="", base_name="",
                                        max_modalities=_TICKET_MAX_MODALITIES):
    """Every bookable combination of a ticket's extra costs, as Modalities.

    CONFIRMED PRODUCT-OWNER RULE: **Tickets do not have supplements.** Every extra cost is its own
    Modality, priced at the base price PLUS that extra - so a ticket with a base (English guide)
    rate and a German-guide surcharge becomes two Modalities, at base and base+surcharge, not one
    Modality with a supplement bolted on. Confirmed follow-up: when a ticket has SEVERAL extras,
    generate EVERY combination, with the app doing the addition.

    GROUPS ARE WHAT KEEP THIS SANE. Options sharing a `group` are mutually exclusive alternatives
    (you cannot have an English guide and a German guide on the same booking), so each group
    contributes at most one option to a combination. Options in different groups combine freely.
    Without groups, "every combination" would generate an English+German modality, which is not a
    product anyone can sell.

    The all-base combination is always first and always carries the base code/name unchanged, so
    updating an existing ticket never renames or re-codes the modality already live.

    Returns a list of dicts: code, name, adult_price, children_price, infant_price, extras
    (the option names chosen), is_base, plus a `dropped` count on the result of any cap applied.
    """
    base_adult = _safe_float((base_prices or {}).get("adult", 0))
    base_child = _safe_float((base_prices or {}).get("children", 0))
    base_infant = _safe_float((base_prices or {}).get("infant", 0))

    groups, order = {}, []
    for opt in (extra_options or []):
        if not isinstance(opt, dict):
            continue
        name = str(opt.get("name") or "").strip()
        if not name:
            continue
        # An option with no group is independently choosable, so it gets a group of its own
        # rather than being lumped in with every other ungrouped extra (which would wrongly
        # make a lunch upgrade and a photo package mutually exclusive).
        group = str(opt.get("group") or "").strip() or f"__solo__{name}"
        if group not in groups:
            groups[group] = []
            order.append(group)
        groups[group].append(opt)

    combos = [{"extras": [], "adult": base_adult, "children": base_child, "infant": base_infant}]
    for group in order:
        grown = []
        for combo in combos:
            grown.append(combo)                       # this group not chosen
            for opt in groups[group]:
                grown.append({
                    "extras": combo["extras"] + [str(opt.get("name")).strip()],
                    "adult": combo["adult"] + _safe_float(opt.get("adult_price", 0)),
                    "children": combo["children"] + _safe_float(opt.get("children_price", 0)),
                    "infant": combo["infant"] + _safe_float(opt.get("infant_price", 0)),
                })
        combos = grown

    # Fewest extras first, so the base and the simple single-extra products lead the list and
    # are the ones that survive if the cap bites.
    combos.sort(key=lambda c: (len(c["extras"]), c["extras"]))
    dropped = max(0, len(combos) - max(1, int(max_modalities)))
    combos = combos[:max(1, int(max_modalities))]

    out = []
    for combo in combos:
        is_base = not combo["extras"]
        code = base_code if is_base else (
            (base_code or "TKT") + "".join(_ticket_extra_token(e) for e in combo["extras"]))
        name = base_name if is_base else f"{base_name} ({', '.join(combo['extras'])})".strip()
        out.append({
            "code": code, "name": name, "extras": list(combo["extras"]), "is_base": is_base,
            "adult_price": round(combo["adult"], 2), "children_price": round(combo["children"], 2),
            "infant_price": round(combo["infant"], 2), "dropped": dropped,
        })
    return out


def place_short_code(name: str) -> str:
    """A short, readable code for a place, for building modality codes.

    CONFIRMED REAL CONVENTION (product owner, from live options LUXHRG1 / LUXHRG29): the code
    is the two places' short codes run together with the pax range, e.g. Luxor + Hurghada +
    "1". Known places use the operator's own form - Hurghada is HRG, not HUR - and anything
    else falls back to its first three letters, which is what makes this work for a
    destination nobody has listed yet."""
    clean = re.sub(r"(?i)\b(international|intl\.?|airport|city|port)\b", " ", name or "")
    clean = " ".join(clean.split()).strip()
    mapped = _PLACE_SHORT_CODE.get(clean.lower())
    if mapped:
        return mapped
    letters = re.sub(r"[^A-Za-z]", "", clean).upper()
    return letters[:3] or "XXX"


def _generate_transport_option_code(departure_name: str, arrival_name: str, min_occ: int, max_occ: int) -> str:
    """A modality code in the operator's own style: LUXHRG1, LUXHRG29.

    CONFIRMED against real live options on TRANSPORT-415750. Two different routes could in
    principle abbreviate to the same pair; that is tolerable because matching an existing
    option on UPDATE never relies on this code - see
    transport_matcher.match_bracket_to_existing_option() - so a collision costs readability,
    never correctness."""
    route = f"{place_short_code(departure_name)}{place_short_code(arrival_name)}"
    bracket = f"{min_occ}" if min_occ == max_occ else f"{min_occ}{max_occ}"
    return f"{route}{bracket}"


def build_transport_payloads(
    pre_config: TransportHumanPreConfig,
    extracted_transport_data: Dict[str, Any],
    api_client: TravelCompositorAPI,
    existing_transport_id: str = None,
    existing_transport_snapshot: Dict[str, Any] = None,
    existing_options_snapshot: List[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Builds BOTH the main ContractTransportVO payload and one ContractTransportOptionVO payload
    per occupancy bracket, given human pre-config + AI-extracted document data + Travel
    Compositor's Transport Base location lookup (api_client.resolve_transport_base). Mirrors
    build_transfer_payload's structure/error-handling conventions, adapted for Transport's
    two-level (parent + options) shape.

    UNLIKE Transfer, Transport has genuinely separate Option sub-resources for pricing - so this
    returns a LIST of option actions (create new / update existing / deactivate orphaned), not a
    single combined payload. The caller (app.py) calls create_transport/update_transport for the
    parent FIRST, then create_transport_option/update_transport_option/deactivate for each option
    action (a fresh create needs the parent's real 'id', only known after the parent step).

    NOTE: ContractTransportVO has NO supplements/additionalServices field at all in the confirmed
    Swagger - unlike Transfer, there's nothing equivalent to build here for optional/mandatory
    surcharges; Transport's schema simply doesn't have anywhere to put them.

    UNCONFIRMED ASSUMPTION (no real per-vehicle Transport example exists to verify against -
    every real example seen has pricePerPax=true): ContractTransportOptionPriceVO only has
    adult/children/infant supplement fields, no generic "vehicle" field, so a per-vehicle
    bracket's delta is written into adultPriceSupplement the same way a per-pax bracket's is -
    it's the only numeric delta field the schema offers. Flag this if a real per-vehicle
    Transport publish ever gets rejected or behaves unexpectedly.

    CONFIRMED MERGE-ON-UPDATE (product owner, same rule as Transfer): pass
    existing_transport_snapshot (the current live GET /transport/{supplierId}/{transportId})
    when updating, so this preserves the existing startDate/endDate/images instead of
    overwriting them with this rate sheet's own season-specific values. Pass
    existing_options_snapshot (every existing option, fetched via get_transport_option for each
    of the parent's optionCodes) so each bracket can be matched to its corresponding existing
    option (by minPassengers/maxPassengers overlap - see transport_matcher.
    match_bracket_to_existing_option, since real option codes are never predictable) rather than
    creating a duplicate; any existing option no longer covered by this rate sheet is returned in
    options_to_deactivate (CONFIRMED product owner rule: "we cannot sell something, that we have
    no prices" - set active: false rather than leaving a stale price live or deleting it, since
    there is no DELETE endpoint).
    """
    departure_name = (extracted_transport_data.get("departure_name") or "").strip()
    arrival_name = (extracted_transport_data.get("arrival_name") or "").strip()

    departure_base = api_client.resolve_transport_base(departure_name) if departure_name else \
        {"code": None, "name": departure_name, "valid": False, "match_type": "empty_query"}
    arrival_base = api_client.resolve_transport_base(arrival_name) if arrival_name else \
        {"code": None, "name": arrival_name, "valid": False, "match_type": "empty_query"}

    currency = extracted_transport_data.get("currency") or pre_config.currency
    currency, _currency_inherited = _locked_on_update(
        existing_transport_snapshot, "currency", currency)
    charge_unit = (extracted_transport_data.get("charge_unit") or "per_pax").lower()
    price_per_pax = charge_unit != "per_service"

    # Cancellation: Transport has a genuine structured field (unlike Transfer) - still reuses
    # the shared cross-product voucher-text rule (_cancellation_voucher_text) since the AI's
    # source-stated text/tiers are exactly the same shape either way.
    cancellation_tiers = _cancellation_ranges_from_tiers(extracted_transport_data.get("cancellation_policy_tiers"))
    cancellation_ranges = (
        [ContractTransportCancellationRangeVO(days=d, percentage=p) for d, p in cancellation_tiers]
        if cancellation_tiers else [ContractTransportCancellationRangeVO()]
    )
    voucher_text = _with_manual_notes(
        _cancellation_voucher_text(extracted_transport_data.get("cancellation_policy_text"), cancellation_tiers),
        extracted_transport_data)

    # Occupancy brackets: drop/clip anything beyond the 9-pax system cap (CONFIRMED product
    # owner rule, applies "for all services"), then apply the multi-vehicle synthesis rule.
    raw_brackets = [
        b for b in (extracted_transport_data.get("occupancy_brackets") or [])
        if isinstance(b, dict) and _safe_int(b.get("min_occupancy", 1), fallback=1) <= _MAX_OCCUPANCY_PAX
    ]
    brackets = []
    for b in raw_brackets:
        b = dict(b)
        b["min_occupancy"] = _safe_int(b.get("min_occupancy", 1), fallback=1)
        b["max_occupancy"] = min(
            _safe_int(b.get("max_occupancy", b["min_occupancy"]), fallback=b["min_occupancy"]),
            _MAX_OCCUPANCY_PAX,
        )
        brackets.append(b)
    brackets_sorted = sorted(brackets, key=lambda x: x["min_occupancy"])
    brackets_sorted = _add_minimum_charge_bracket(
        brackets_sorted, price_per_pax, extracted_transport_data.get("min_billable_pax"))
    brackets_sorted = _extend_transport_brackets_for_multi_vehicle_pricing(brackets_sorted, price_per_pax)

    # CONFIRMED (via real data): unlike Transfer (which always writes every tier explicitly, so
    # "which one is base" barely matters), Transport's base price only ever shows up combined
    # with a bracket's own supplement - the customer never sees it standalone - and empty
    # `prices` (no supplement at all) is reserved for whichever bracket the source's numbers
    # happen to equal exactly. Picking the WIDEST occupancy bracket as base_price (rather than
    # simply the smallest occupancy) matches this: the real Aswan-Hurghada example had a narrow
    # 1-pax outlier bracket (width 0) and a wide 2-9 pax bracket (width 7) that shared the exact
    # value later used as baseAdultPrice - selecting by width reproduces that exactly (base=90,
    # not the 1-pax bracket's 180), so the common/majority-width bracket ends up with an empty
    # prices array and only the genuine outlier(s) carry an explicit supplement. Ties (e.g. every
    # bracket the same width, as in the real Praslin-La Digue 4-bracket example) break toward the
    # smallest occupancy, deterministically - any tied choice is mathematically equivalent since
    # every bracket's final price is always base+supplement regardless of which one is "base".
    base_bracket = (
        max(brackets_sorted, key=lambda b: (b["max_occupancy"] - b["min_occupancy"], -b["min_occupancy"]))
        if brackets_sorted else None
    )
    base_price = _safe_float(base_bracket.get("price", 0)) if base_bracket else 0.0
    base_child_price = _safe_float(base_bracket.get("child_price")) if base_bracket and base_bracket.get("child_price") is not None else 0.0
    base_infant_price = _safe_float(base_bracket.get("infant_price")) if base_bracket and base_bracket.get("infant_price") is not None else 0.0

    # House naming: "DEPARTURE - ARRIVAL" (confirmed product-owner template). The service class
    # lives in the description and in the modality codes, not in the product name, so two
    # classes on one route do not produce two products with the same name.
    transport_name = transport_display_name(departure_name, arrival_name) or \
        extracted_transport_data.get("service_name") or ""

    # CONFIRMED (via real Swagger): ContractTransportDataSheetVO only has name/description - no
    # dedicated voucherRemarks-style field the way ClosedTour/Ticket/Transfer have. The
    # cancellation text still needs to reach customer-facing text somewhere (same universal rule
    # - see _cancellation_voucher_text's docstring), so it's appended to description instead.
    # The house one-sentence description, unless a human has edited it on the review screen.
    # `description_is_custom` is set there; without it, re-rendering would silently overwrite
    # an edit with the template again.
    description_text = (extracted_transport_data.get("description") or "").strip()
    if not extracted_transport_data.get("description_is_custom"):
        description_text = transport_description(
            extracted_transport_data.get("service_name"), departure_name, arrival_name)
    full_description = f"{description_text}\n\n{voucher_text}".strip() if description_text else voucher_text
    # CONFIRMED from the real live record: the EN description is HTML ("<p>...</p>"), not plain
    # text. Sent as plain text it renders as one unbroken run wherever Travel Compositor
    # expects markup.
    if full_description and "<" not in full_description:
        full_description = "".join(f"<p>{para.strip()}</p>"
                                   for para in full_description.split("\n\n") if para.strip())
    datasheet_en = TransportDataSheetVO(name=transport_name, description=full_description)

    # Arrival is DERIVED from departure + duration whenever a duration is known, rather than
    # taken from whatever is sitting in arrival_time. Both used to default to 09:00, which
    # published every long-distance route as instantaneous.
    _departure_time = normalize_time_hhmmss(extracted_transport_data.get("departure_time") or "09:00:00")
    # Rounded UP to the full hour before anything is derived from it, so the published arrival
    # and the duration always agree.
    _duration = round_duration_up_to_hour(extracted_transport_data.get("duration_time"))
    extracted_transport_data["duration_time"] = _duration
    _derived_arrival, _derived_plus_days = derive_arrival_from_duration(_departure_time, _duration)
    _arrival_time = _derived_arrival or normalize_time_hhmmss(
        extracted_transport_data.get("arrival_time") or "09:00:00")
    _plus_days = (_derived_plus_days if _derived_arrival is not None
                  else _safe_int(extracted_transport_data.get("plus_days", 0), fallback=0))

    segment = TransportSegmentVO(
        departureLocationCode=departure_base.get("code") or "",
        arrivalLocationCode=arrival_base.get("code") or "",
        departureTime=_departure_time,
        arrivalTime=_arrival_time,
        plusDays=_plus_days,
        durationTime=(normalize_time_hhmmss(extracted_transport_data["duration_time"])
                      if extracted_transport_data.get("duration_time") else None),
        model=extracted_transport_data.get("vehicle_model") or None,
        numService=extracted_transport_data.get("service_number") or None,
    )

    # CONFIRMED MERGE-ON-UPDATE (product owner, same rule as Transfer's build_transfer_payload):
    # preserve the existing live record's dates/images on an update rather than overwriting them
    # with this rate sheet's own season-specific values.
    if existing_transport_snapshot:
        effective_start_date = existing_transport_snapshot.get("startDate") or extracted_transport_data.get("start_date") or ""
        effective_end_date = existing_transport_snapshot.get("endDate") or extracted_transport_data.get("end_date") or ""
        effective_images = existing_transport_snapshot.get("images") or []
    else:
        effective_start_date = start_date_or_today(extracted_transport_data.get("start_date"))
        effective_end_date = extracted_transport_data.get("end_date") or ""
        effective_images = []

    transport_payload = None
    transport_error = None
    try:
        transport_kwargs = dict(
            id=existing_transport_id,
            name=transport_name,
            segments=[segment],
            transportType=_map_transport_type(extracted_transport_data.get("transport_type_hint"), transport_name),
            datasheets={"EN": datasheet_en},
            images=effective_images,
            pricePerPax=price_per_pax,
            currency=currency,
            vehiclePrice=0.0 if price_per_pax else base_price,
            baseAdultPrice=base_price if price_per_pax else 0.0,
            baseChildrenPrice=(base_child_price if price_per_pax else 0.0),
            baseInfantPrice=(base_infant_price if price_per_pax else 0.0),
            startDate=effective_start_date,
            endDate=effective_end_date,
            releaseContract=pre_config.days_available_before_release,
            optionCodes=[],  # populated below once bracket codes are known
            allowOWPrice=True,
            allowRTPrice=False,  # RT deprioritized (product owner) - no real example has RT enabled
            companyName=extracted_transport_data.get("company_name") or "",
            cancellationRanges=cancellation_ranges,
        )
        transport = ContractTransportVO(**transport_kwargs)
        transport_payload = transport.dict()
    except ValidationError as e:
        transport_error = str(e)
    except (ValueError, TypeError) as e:
        transport_error = f"Couldn't build the transport payload - {e}"

    # --- Options: one per occupancy bracket, matched against existing options (if updating) so
    # a price refresh updates in place instead of creating duplicates - see
    # transport_matcher.match_bracket_to_existing_option's docstring for why this matches by
    # minPassengers/maxPassengers overlap rather than by code.
    option_actions = []
    options_to_deactivate = []
    matched_existing_codes = set()
    option_codes = []

    for b in brackets_sorted:
        min_occ, max_occ = b["min_occupancy"], b["max_occupancy"]
        bracket_price = _safe_float(b.get("price", 0))
        adult_delta = round(bracket_price - base_price, 2)
        children_delta = 0.0
        if b.get("child_price") is not None:
            children_delta = round(_safe_float(b.get("child_price")) - base_child_price, 2)
        infant_delta = 0.0
        if b.get("infant_price") is not None:
            infant_delta = round(_safe_float(b.get("infant_price")) - base_infant_price, 2)

        # CONFIRMED SEMANTICS (product owner, corrected from an initial wrong guess): a bracket
        # that costs exactly the base rate gets NO price entries at all (matches the real
        # ASWHRG 2-9 pax example, prices=[]) rather than a redundant zero-supplement entry.
        prices = []
        if adult_delta != 0 or children_delta != 0 or infant_delta != 0:
            prices = [ContractTransportOptionPriceVO(
                startDate=effective_start_date or extracted_transport_data.get("start_date") or "",
                adultPriceSupplement=adult_delta,
                childrenPriceSupplement=children_delta,
                infantPriceSupplement=infant_delta,
            )]

        matched_existing = None
        if existing_options_snapshot:
            matched_existing = transport_matcher.match_bracket_to_existing_option(
                min_occ, max_occ, existing_options_snapshot
            )
        if matched_existing:
            code = matched_existing.get("code") or _generate_transport_option_code(departure_name, arrival_name, min_occ, max_occ)
            matched_existing_codes.add(matched_existing.get("code"))
            action = "update"
        else:
            code = _generate_transport_option_code(departure_name, arrival_name, min_occ, max_occ)
            action = "create"

        # CONFIRMED against a real live option (TRANSPORT-415750): the modality name names the
        # SERVICE CLASS and the pax range, not the route - "Private Transfer - 1 Pax - Door to
        # Door (no Guide)". The route is already the product's own name, so repeating it here
        # made every modality read "Luxor - Hurghada - 1 Pax" and pushed the one thing that
        # distinguishes the modalities to the end.
        bracket_label = f"{min_occ} Pax" if min_occ == max_occ else f"{min_occ} to {max_occ} Pax"
        _class = (extracted_transport_data.get("service_name") or "").strip() or "Transfer"
        _guide = "with Guide" if "guide" in _class.lower() else "no Guide"
        option_name = f"{_class} - {bracket_label} - Door to Door ({_guide})"
        # A human can override it per modality on the review screen. CONFIRMED REAL RULE
        # (product owner): "the human shall manually add this field." The generated name is a
        # starting point, not the answer - only a person knows whether this particular run
        # carries a guide, or is door-to-door, or is something the pattern has no word for.
        _override = (extracted_transport_data.get("modality_names") or {}).get(
            f"{min_occ}-{max_occ}")
        if isinstance(_override, str) and _override.strip():
            option_name = _override.strip()

        option_error = None
        option_payload = None
        try:
            option = ContractTransportOptionVO(
                code=code,
                # CONFIRMED from the real live options: one piece per passenger. Previously
                # left unset because no example had ever shown them populated.
                baggageAllowance="1",
                baggageAllowanceType="PC",
                minPassengers=min_occ,
                maxPassengers=max_occ,
                prices=prices,
                # quantity 0 = unlimited (CONFIRMED product owner). One live record had 99 on
                # one modality and 0 on another; that difference was not meaningful, so every
                # modality of a transport gets the same unlimited allocation.
                inventories=[ContractTransportOptionInventoryVO(
                    inventoryDate=LocalDateRangeVO(start=effective_start_date or extracted_transport_data.get("start_date") or ""),
                    quantity=0,
                )],
                translations={"EN": TransportDataSheetVO(name=option_name)},
            )
            option_payload = option.dict()
        except ValidationError as e:
            option_error = str(e)
        except (ValueError, TypeError) as e:
            option_error = f"Couldn't build option for bracket {min_occ}-{max_occ} - {e}"

        option_codes.append(code)
        option_actions.append({
            "action": action,
            "code": code,
            "min_occupancy": min_occ,
            "max_occupancy": max_occ,
            "option_payload": option_payload,
            "option_error": option_error,
        })

    if transport_payload is not None:
        transport_payload["optionCodes"] = option_codes

    # Any existing option whose bracket is no longer covered by this rate sheet gets deactivated
    # rather than left stale or deleted (no DELETE endpoint exists) - CONFIRMED product owner
    # rule: "we cannot sell something, that we have no prices."
    for existing_opt in (existing_options_snapshot or []):
        if not isinstance(existing_opt, dict):
            continue
        if existing_opt.get("code") not in matched_existing_codes:
            deactivated = dict(existing_opt)
            deactivated["active"] = False
            options_to_deactivate.append(deactivated)

    return {
        "supplier_id": pre_config.supplier_id,
        "transport_payload": transport_payload,
        "transport_error": transport_error,
        "transport_name": transport_name,
        "departure_name": departure_name,
        "arrival_name": arrival_name,
        "departure_base_resolved": departure_base.get("valid", False),
        "departure_base_match_type": departure_base.get("match_type"),
        # Which name actually matched, when it wasn't the one in the document. An airport
        # resolved via its city ("RMF Airport" -> "Marsa Alam") is a substitution a human
        # should see rather than discover later on a published route.
        "departure_base_resolved_via": departure_base.get("resolved_via"),
        "departure_base_name": departure_base.get("name"),
        "arrival_base_resolved": arrival_base.get("valid", False),
        "arrival_base_match_type": arrival_base.get("match_type"),
        "arrival_base_resolved_via": arrival_base.get("resolved_via"),
        "arrival_base_name": arrival_base.get("name"),
        "existing_transport_id": existing_transport_id,
        "option_actions": option_actions,
        "options_to_deactivate": options_to_deactivate,
    }


# ==========================================
# HOTEL BUILDER
# Confirmed against the real Contract Hotel Swagger + 2 real GET pulls for a
# live hotel (CAI-H1, Four Seasons Hotel Cairo at Nile Plaza, supplier
# 48940). See schemas.py's HOTEL SCHEMAS section for the full field-by-field
# confirmation notes and every flagged assumption.
#
# TWO-PHASE BUILD, unlike every other product type built so far - a real
# sequencing constraint, not a design choice: a NEW room's providerCode is
# system-generated (AUTO_...) and only comes back in the hotel create/update
# RESPONSE, so rate payloads (which reference rooms by providerCode in
# seasonRoomPrices) cannot be built until AFTER the hotel contract call has
# actually been submitted and its response inspected. Mirrors the same
# create-parent-then-create-children sequencing already used for Transport
# (transport id -> option payloads), just one level further removed:
#   Phase 1: build_hotel_contract_payload()  -> submit via
#            api_client.create_hotel()/update_hotel() -> inspect the
#            response's rooms[] to resolve {room_name: providerCode}
#   Phase 2: build_hotel_offer_payloads() / build_hotel_supplement_payloads()
#            (submit each via create_hotel_offer()/create_hotel_supplement())
#            then build_hotel_rate_payloads() (needs the offer/supplement
#            provider codes just created, plus the Phase-1 room codes) ->
#            submit via create_hotel_rates()/update_hotel_rates().
# ==========================================

_MEAL_PLAN_TYPES = ["ROOM_ONLY", "BED_AND_BREAKFAST", "HALF_BOARD", "FULL_BOARD", "ALL_INCLUSIVE"]

_MEAL_PLAN_KEYWORDS = {
    "ROOM_ONLY": ["room only", "no meals", "self catering", "self-catering", "european plan", " ep "],
    "BED_AND_BREAKFAST": ["breakfast", "bed and breakfast", "b&b", "bb", "continental breakfast"],
    "HALF_BOARD": ["half board", "half-board", "modified american", "dinner, bed and breakfast", "dbb", " hb", "hb "],
    "FULL_BOARD": ["full board", "full-board", "american plan", "three meals", " fb", "fb "],
    "ALL_INCLUSIVE": ["all inclusive", "all-inclusive", " ai", "ai "],
}


def _map_meal_plan_type(hint):
    """Maps free-text meal-plan wording (e.g. 'Half Board', 'Breakfast included') onto the
    confirmed 5-value MealPlanType enum. Defaults to ROOM_ONLY when nothing matches, since that's
    the confirmed baseline every hotel starts from (product owner: 'if no other stated, the Room
    only is always taken as 0 money')."""
    text = f" {(hint or '').strip().lower()} "
    if text.strip().upper() in _MEAL_PLAN_TYPES:
        return text.strip().upper()
    for plan_type, keywords in _MEAL_PLAN_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return plan_type
    return "ROOM_ONLY"


_APPLY_TYPE_VALUES = ["LODGING", "MEAL", "LODGING_AND_MEAL", "PER_NIGHT", "PER_NIGHT_PERSON", "PER_STAY", "PER_STAY_PERSON"]


def _map_apply_type(hint, default="LODGING"):
    """Validates/normalizes an Offer/Supplement 'apply' value against the confirmed 7-value enum
    (mixing what-it-applies-to and how-it's-calculated into one flat field - see
    ContractHotelOffersVO's docstring).

    OFFERS default to LODGING (the most common case - a straightforward room-rate discount).
    SUPPLEMENTS pass default=None instead: CONFIRMED PRODUCT-OWNER RULE - the basis of a hotel
    supplement is never guessed. "Per person" alone could mean once per person for the stay or
    once per person per night, and those differ by a factor of the whole stay length; a wrong
    guess overcharges the client on every booking. Unrecognised input returns None so the caller
    can stop and ask a human rather than publishing a plausible-looking number."""
    text = (hint or "").strip().upper().replace(" ", "_").replace("-", "_")
    return text if text in _APPLY_TYPE_VALUES else default


def _map_offer_type(hint):
    """Validates/normalizes against the confirmed 3-value OfferType enum, defaulting to PERCENT."""
    text = (hint or "").strip().upper().replace(" ", "_")
    return text if text in ("PERCENT", "ABSOLUTE", "STAY_TO_PAY") else "PERCENT"


def _map_supplement_type(hint):
    """Validates/normalizes against the confirmed 2-value SupplementType enum (no STAY_TO_PAY -
    that's offer-only), defaulting to ABSOLUTE."""
    text = (hint or "").strip().upper().replace(" ", "_")
    return text if text in ("PERCENT", "ABSOLUTE") else "ABSOLUTE"


def _map_price_type(hint):
    """Validates/normalizes a season's pricing model against the confirmed 2-value enum,
    defaulting to DISTRIBUTION - the model used in every real example seen (product owner
    confirmed the perfectly-arithmetic real example was demo data, but DISTRIBUTION itself, i.e.
    one flat amount per adult+children combo, is the real, live pricing model)."""
    text = (hint or "").strip().upper()
    return text if text in ("PAX", "DISTRIBUTION") else "DISTRIBUTION"


def _translation_list(text, language="EN"):
    """Wraps a single text string into Hotel's [TranslationVO] shape ({language, description}
    pairs) - used for descriptions/voucherRemarks/offer&supplement names. Returns [] for blank
    text so an empty field isn't sent as a meaningless single empty-string entry."""
    clean = (text or "").strip()
    if not clean:
        return []
    return [TranslationVO(language=language, description=clean)]


def _translation_list_or_existing(new_text, existing_translations):
    """Prefers a fresh document's own text; falls back to preserving whatever existing
    descriptions/voucherRemarks were already on the live record, rather than blanking them out
    just because the new document doesn't happen to restate them (same merge-on-update
    philosophy as Transfer/Transport preserving startDate/endDate/images)."""
    fresh = _translation_list(new_text)
    if fresh:
        return fresh
    return [TranslationVO(**t) for t in (existing_translations or []) if isinstance(t, dict)]


def _clip_distributions_to_pax_cap(distributions, max_cap=_MAX_OCCUPANCY_PAX):
    """CONFIRMED REAL RULE (product owner, applies 'for all services'): drops any distribution
    whose total occupancy (adults+children) exceeds the 9-pax system cap - Travel Compositor
    genuinely can't sell it. `distributions` is a list of {"adults": int, "children": int}."""
    kept = []
    for d in distributions or []:
        if not isinstance(d, dict):
            continue
        adults = _safe_int(d.get("adults", 0))
        children = _safe_int(d.get("children", 0))
        if adults < 1 or adults + children > max_cap:
            continue
        kept.append({"adults": adults, "children": children})
    return kept


def _clip_distributions_to_pax_cap_priced(distribution_prices, max_cap=_MAX_OCCUPANCY_PAX):
    """Same 9-pax cap rule as _clip_distributions_to_pax_cap, but for PRICED distribution entries
    (adults/children/amount) used in seasonRoomPrices.distributionPrices."""
    kept = []
    for p in distribution_prices or []:
        if not isinstance(p, dict):
            continue
        adults = _safe_int(p.get("adults", 0))
        children = _safe_int(p.get("children", 0))
        if adults < 1 or adults + children > max_cap:
            continue
        kept.append(p)
    return kept


def _ensure_room_only_meal_plan(meal_plans_data):
    """CONFIRMED REAL RULE (product owner): 'the Room only is always taken as 0 money' - ensures
    a ROOM_ONLY entry is always present at 0 cost, even if the source document never explicitly
    mentions a room-only/no-meals option (most documents only describe the paid add-on plans)."""
    result = list(meal_plans_data or [])
    has_room_only = any(_map_meal_plan_type((mp or {}).get("meal_plan_hint")) == "ROOM_ONLY" for mp in result)
    if not has_room_only:
        result.insert(0, {"meal_plan_hint": "ROOM_ONLY", "base_price": 0.0, "adult_prices": [], "child_prices": []})
    return result


def _build_meal_plan_payload(mp_data):
    plan_type = _map_meal_plan_type((mp_data or {}).get("meal_plan_hint"))
    if plan_type == "ROOM_ONLY":
        # CONFIRMED REAL RULE: always 0-cost, regardless of anything else extracted for it.
        return ContractMealPlanVO(mealPlan="ROOM_ONLY", basePrice=0.0, adultPrices=[], childPrices=[])
    return ContractMealPlanVO(
        mealPlan=plan_type,
        basePrice=_safe_float((mp_data or {}).get("base_price", 0)),
        adultPrices=[_safe_float(p) for p in (mp_data or {}).get("adult_prices") or []],
        childPrices=[_safe_float(p) for p in (mp_data or {}).get("child_prices") or []],
    )


def _build_room_payload(room_data, existing_room=None):
    """Builds a ContractRoomVO. If `existing_room` (a matched real room dict from
    hotel_matcher.match_room_by_name) is given, reuses its providerCode so Travel Compositor
    recognizes this as the SAME room rather than creating a duplicate - CONFIRMED (product
    owner): a room's providerCode is system-generated (AUTO_...) and never set by this tool."""
    distributions = _clip_distributions_to_pax_cap((room_data or {}).get("distributions") or [])
    return ContractRoomVO(
        name=(room_data or {}).get("name"),
        typeId=(room_data or {}).get("type_id"),
        providerCode=(existing_room or {}).get("providerCode") if existing_room else None,
        distributions=[ContractRoomDistributionVO(adults=d["adults"], children=d["children"]) for d in distributions],
    )


def build_hotel_contract_payload(pre_config, extracted_hotel_data, existing_hotel_snapshot=None):
    """
    PHASE 1 of the two-phase Hotel build - see the section docstring above. Builds the hotel-
    level ContractHotelVO payload (hotel fields + rooms[] + mealPlans[] + descriptions +
    voucherRemarks + images), ready for api_client.create_hotel() (existing_hotel_snapshot=None)
    or api_client.update_hotel() (existing_hotel_snapshot = a real GET /hotel/{supplierId}/
    {providerCode} response dict).

    MERGE-ON-UPDATE (same philosophy as Transfer/Transport): PUT replaces the ENTIRE rooms[]/
    mealPlans[] arrays, so any existing room/meal-plan not mentioned in the fresh document would
    otherwise be silently dropped. Existing rooms are matched by name (hotel_matcher.
    match_room_by_name) and merged in - a document's room with a matching name UPDATES that
    room's distributions in place (reusing its existing providerCode); a document's room with no
    match is a brand-new room; any EXISTING room the fresh document doesn't mention at all is
    still carried forward unchanged, never dropped.

    Returns {"hotel_payload": dict|None, "hotel_error": str|None, "is_update": bool,
             "room_name_matches": {room_name: existing_providerCode_or_None}}.
    """
    extracted = extracted_hotel_data or {}
    is_update = existing_hotel_snapshot is not None
    existing_rooms = (existing_hotel_snapshot or {}).get("rooms") or []
    existing_meal_plans = (existing_hotel_snapshot or {}).get("mealPlans") or []

    # ---- Rooms: merge new/updated rooms with any existing rooms the fresh document doesn't mention ----
    document_rooms = extracted.get("rooms") or []
    seen_room_names = set()
    room_payloads = []
    room_name_matches = {}
    for room_data in document_rooms:
        room_name = (room_data or {}).get("name")
        existing_room = hotel_matcher.match_room_by_name(room_name, existing_rooms)
        room_payloads.append(_build_room_payload(room_data, existing_room=existing_room))
        room_name_matches[room_name] = (existing_room or {}).get("providerCode")
        if room_name:
            seen_room_names.add((room_name or "").strip().lower())

    for existing_room in existing_rooms:
        if not isinstance(existing_room, dict):
            continue
        if (existing_room.get("name") or "").strip().lower() not in seen_room_names:
            # Carried forward unchanged - not mentioned in this document, but never silently dropped.
            room_payloads.append(ContractRoomVO(
                name=existing_room.get("name"),
                typeId=existing_room.get("typeId"),
                providerCode=existing_room.get("providerCode"),
                distributions=[ContractRoomDistributionVO(adults=d.get("adults", 1), children=d.get("children", 0))
                               for d in existing_room.get("distributions") or []],
            ))

    # ---- Meal plans: same carry-forward merge, keyed by mealPlan type (only one entry per type) ----
    document_meal_plans = _ensure_room_only_meal_plan(extracted.get("meal_plans") or [])
    meal_plan_payloads = []
    seen_plan_types = set()
    for mp_data in document_meal_plans:
        payload = _build_meal_plan_payload(mp_data)
        meal_plan_payloads.append(payload)
        seen_plan_types.add(payload.mealPlan)

    for existing_mp in existing_meal_plans:
        if not isinstance(existing_mp, dict):
            continue
        if existing_mp.get("mealPlan") not in seen_plan_types:
            meal_plan_payloads.append(ContractMealPlanVO(
                mealPlan=existing_mp.get("mealPlan", "ROOM_ONLY"),
                basePrice=_safe_float(existing_mp.get("basePrice", 0)),
                adultPrices=[_safe_float(p) for p in existing_mp.get("adultPrices") or []],
                childPrices=[_safe_float(p) for p in existing_mp.get("childPrices") or []],
            ))

    # ---- Cancellation text + manual notes -> voucherRemarks only (CONFIRMED: no structured
    # cancellation field exists on Hotel) ----
    voucher_text = _with_manual_notes(
        _cancellation_voucher_text(
            extracted.get("cancellation_policy_text"),
            extracted.get("cancellation_policy_tiers"),
        ),
        extracted,
    )

    address_data = extracted.get("address") or {}
    existing_address = (existing_hotel_snapshot or {}).get("address") or {}

    hotel_kwargs = dict(
        providerCode=pre_config.provider_code,
        hotelname=extracted.get("hotelname") or (existing_hotel_snapshot or {}).get("hotelname") or "",
        latitude=extracted.get("latitude", (existing_hotel_snapshot or {}).get("latitude")),
        longitude=extracted.get("longitude", (existing_hotel_snapshot or {}).get("longitude")),
        address=HotelAddressVO(
            address=address_data.get("address") or existing_address.get("address"),
            locationName=address_data.get("location_name") or existing_address.get("locationName"),
            postalCode=address_data.get("postal_code") or existing_address.get("postalCode"),
            country=address_data.get("country") or existing_address.get("country"),
            phone=address_data.get("phone") or existing_address.get("phone"),
            fax=address_data.get("fax") or existing_address.get("fax"),
            email=address_data.get("email") or existing_address.get("email"),
        ),
        category=extracted.get("category") or (existing_hotel_snapshot or {}).get("category") or "",
        chain=extracted.get("chain") or (existing_hotel_snapshot or {}).get("chain"),
        # A live hotel contract's currency is already set; the Step-2 dropdown must not
        # re-denominate it. See _locked_on_update.
        currency=_locked_on_update(existing_hotel_snapshot, "currency", pre_config.currency)[0],
        releaseDays=_safe_int(extracted.get("release_days", pre_config.days_available_before_release), fallback=pre_config.days_available_before_release),
        minimumStay=_safe_int(extracted.get("minimum_stay", 1), fallback=1),
        maximumStay=extracted.get("maximum_stay"),
        infantsAllowed=_safe_int(extracted.get("infants_allowed", 2), fallback=2),
        minimumChildrenAge=_safe_int(extracted.get("min_children_age", 0), fallback=0),
        maximumChildrenAge=_safe_int(extracted.get("max_children_age", 12), fallback=12),
        rooms=room_payloads,
        mealPlans=meal_plan_payloads,
        descriptions=_translation_list_or_existing(extracted.get("description"), (existing_hotel_snapshot or {}).get("descriptions")),
        voucherRemarks=_translation_list_or_existing(voucher_text, (existing_hotel_snapshot or {}).get("voucherRemarks")),
        images=extracted.get("images") or (existing_hotel_snapshot or {}).get("images") or [],
    )

    hotel_error = None
    hotel_payload = None
    try:
        hotel = ContractHotelVO(**hotel_kwargs)
        hotel_payload = hotel.dict()
    except ValidationError as e:
        hotel_error = str(e)
    except (ValueError, TypeError) as e:
        hotel_error = f"Couldn't build hotel contract payload - {e}"

    return {
        "hotel_payload": hotel_payload,
        "hotel_error": hotel_error,
        "is_update": is_update,
        "room_name_matches": room_name_matches,
    }


def resolve_room_provider_codes(hotel_response_rooms):
    """Call after Phase 1's create_hotel()/update_hotel() response is in hand - builds the
    {room_name: providerCode} map Phase 2 needs (offers/supplements/rates all reference rooms by
    name in the extracted data, but the real API needs the resolved providerCode, which for a
    brand-new room only exists once Travel Compositor has assigned it in this response)."""
    result = {}
    for room in hotel_response_rooms or []:
        if isinstance(room, dict) and room.get("name"):
            result[room["name"]] = room.get("providerCode")
    return result


def _build_offer_or_supplement_common_kwargs(item_data, apply_default="LODGING"):
    """Shared field-building for Offers and Supplements - they're structurally identical except
    Offers have an extra type value (STAY_TO_PAY) plus stay/pay fields, added by the caller.

    apply_default=None (used for Supplements) makes an unrecognised basis come back as None
    instead of silently becoming LODGING - see _map_apply_type."""
    windows_travel = [w for w in (item_data or {}).get("travel_windows") or [] if isinstance(w, dict) and w.get("start") and w.get("end")]
    windows_booking = [w for w in (item_data or {}).get("booking_windows") or [] if isinstance(w, dict) and w.get("start") and w.get("end")]
    return dict(
        apply=_map_apply_type((item_data or {}).get("apply"), default=apply_default),
        releaseDays=(item_data or {}).get("release_days"),
        minimumStay=(item_data or {}).get("minimum_stay"),
        maximumStay=(item_data or {}).get("maximum_stay"),
        minimumAdults=(item_data or {}).get("minimum_adults"),
        maximumAdults=(item_data or {}).get("maximum_adults"),
        minimumChildrens=(item_data or {}).get("minimum_childrens"),
        maximumChildrens=(item_data or {}).get("maximum_childrens"),
        value=_safe_float((item_data or {}).get("value", 0)),
        childValue=_safe_float((item_data or {}).get("child_value", 0)),
        names=_translation_list((item_data or {}).get("name")),
        travelWindows=[LocalDateRangeVO(start=w["start"], end=w["end"]) for w in windows_travel],
        bookingWindows=[LocalDateRangeVO(start=w["start"], end=w["end"]) for w in windows_booking],
        providerRoomCodes=[c for c in ((item_data or {}).get("room_provider_codes") or []) if c],
        mealPlans=(item_data or {}).get("meal_plans") or [],
        operationalDays=(item_data or {}).get("operational_days") or WEEKDAY_NAMES.copy(),
    )


def build_hotel_offer_payloads(extracted_offers, room_name_to_provider_code, existing_hotel_snapshot=None):
    """
    PHASE 2 (offers). Builds one ContractHotelOffersVO payload per extracted offer, ready for
    api_client.create_hotel_offer() (CONFIRMED create-only, no update path - see
    ContractHotelOffersVO's docstring in schemas.py). Light dedup against existing offers by
    name (hotel_matcher.match_offer_or_supplement_by_name) to avoid re-creating an identical-
    looking offer already on the live record within the same run.

    `room_name_to_provider_code`: {room_name: providerCode}, from resolve_room_provider_codes()
    against Phase 1's create/update RESPONSE - used to translate a document's room-name
    references into the real providerRoomCodes this offer applies to.

    Returns a list of {"offer_payload": dict|None, "offer_error": str|None,
                        "action": "create"|"skip_duplicate", "matched_provider_code": str|None}.
    """
    existing_offers = (existing_hotel_snapshot or {}).get("offers") or []
    results = []
    for offer_data in extracted_offers or []:
        offer_name = (offer_data or {}).get("name")
        existing_match = hotel_matcher.match_offer_or_supplement_by_name(offer_name, existing_offers)
        if existing_match:
            results.append({"offer_payload": None, "offer_error": None, "action": "skip_duplicate",
                             "matched_provider_code": existing_match.get("providerCode")})
            continue

        room_codes = [room_name_to_provider_code.get(rn) for rn in (offer_data or {}).get("room_names") or []]
        kwargs = _build_offer_or_supplement_common_kwargs({**(offer_data or {}), "room_provider_codes": room_codes})
        kwargs["type"] = _map_offer_type((offer_data or {}).get("type"))
        kwargs["stay"] = (offer_data or {}).get("stay")
        kwargs["pay"] = (offer_data or {}).get("pay")

        offer_error = None
        offer_payload = None
        try:
            offer = ContractHotelOffersVO(**kwargs)
            offer_payload = offer.dict()
        except ValidationError as e:
            offer_error = str(e)
        except (ValueError, TypeError) as e:
            offer_error = f"Couldn't build offer '{offer_name}' - {e}"

        results.append({"offer_payload": offer_payload, "offer_error": offer_error, "action": "create",
                         "matched_provider_code": None})
    return results


def build_hotel_supplement_payloads(extracted_supplements, room_name_to_provider_code, existing_hotel_snapshot=None):
    """Same as build_hotel_offer_payloads but for Supplements - see that function's docstring;
    identical mechanics, minus the type=STAY_TO_PAY/stay/pay option since supplements only have
    PERCENT/ABSOLUTE."""
    existing_supplements = (existing_hotel_snapshot or {}).get("supplements") or []
    results = []
    for supp_data in extracted_supplements or []:
        supp_name = (supp_data or {}).get("name")
        existing_match = hotel_matcher.match_offer_or_supplement_by_name(supp_name, existing_supplements)
        if existing_match:
            results.append({"supplement_payload": None, "supplement_error": None, "action": "skip_duplicate",
                             "matched_provider_code": existing_match.get("providerCode")})
            continue

        room_codes = [room_name_to_provider_code.get(rn) for rn in (supp_data or {}).get("room_names") or []]
        kwargs = _build_offer_or_supplement_common_kwargs(
            {**(supp_data or {}), "room_provider_codes": room_codes}, apply_default=None)
        kwargs["type"] = _map_supplement_type((supp_data or {}).get("type"))

        # CONFIRMED PRODUCT-OWNER RULE: never guess a supplement's basis. Stop here with a
        # readable message instead of publishing a charge whose per-night/per-stay meaning
        # nobody actually chose - the two readings differ by the whole length of the stay.
        if not kwargs.get("apply"):
            results.append({
                "supplement_payload": None, "action": "create", "matched_provider_code": None,
                "supplement_error": (
                    f"Supplement '{supp_name or '(unnamed)'}' has no charging basis. Pick one of "
                    f"{', '.join(_APPLY_TYPE_VALUES)} on the review screen - the app will not guess "
                    f"it, because 'per person' can mean once for the whole stay or once per night, "
                    f"and the wrong one overcharges every booking."),
            })
            continue

        supp_error = None
        supp_payload = None
        try:
            supplement = ContractHotelSupplementVO(**kwargs)
            supp_payload = supplement.dict()
        except ValidationError as e:
            supp_error = str(e)
        except (ValueError, TypeError) as e:
            supp_error = f"Couldn't build supplement '{supp_name}' - {e}"

        results.append({"supplement_payload": supp_payload, "supplement_error": supp_error, "action": "create",
                         "matched_provider_code": None})
    return results


def build_hotel_rate_payloads(extracted_rates, room_name_to_provider_code, offer_name_to_provider_code,
                               supplement_name_to_provider_code, existing_hotel_snapshot=None):
    """
    PHASE 2 (rates) - the last step. Builds one ContractHotelRateVO payload per extracted rate-
    group (each with its nested seasons/seasonRoomPrices/stopSales), ready for
    api_client.create_hotel_rates() (new rate) or update_hotel_rates() (existing rate, matched by
    name via hotel_matcher.match_rate_by_name - reuses the existing rate's id, and each matched
    season's existing id via hotel_matcher.match_season_to_existing, so Travel Compositor updates
    in place rather than duplicating).

    `room_name_to_provider_code` / `offer_name_to_provider_code` / `supplement_name_to_provider_
    code`: {name: providerCode} maps - rooms from resolve_room_provider_codes() against Phase 1's
    response, offers/supplements from this run's own build_hotel_offer_payloads()/
    build_hotel_supplement_payloads() results (or matched_provider_code for a skipped duplicate).
    Used to translate the document's own name references into the real provider codes
    seasonRoomPrices/rate.offers/rate.supplements need.

    CONFIRMED REAL RULE (product owner): no deactivation/deletion logic needed for stale
    seasons/rates - "no deleting needed for rates, if the time window is closed, it is done then
    and it cant be sold anymore, so no harm if not deleted."

    STOP SALES: built using roomName ONLY (roomId omitted) - see ContractHotelRoomStopSalesVO's
    docstring in schemas.py for why, and that this is UNCONFIRMED, needing a live validation test
    before being relied on for a real upload.

    A season's room-price entry for a room with no resolvable providerCode is skipped (not sent)
    rather than submitting a rate that references a room Travel Compositor won't recognize.

    Returns a list of {"rate_payload": dict|None, "rate_error": str|None,
                        "action": "create"|"update", "matched_rate_id": int|None,
                        "season_actions": [{"season_name", "action", "matched_season_id"}]}.
    """
    existing_rates = (existing_hotel_snapshot or {}).get("rates") or []
    results = []

    for rate_data in extracted_rates or []:
        rate_name = (rate_data or {}).get("name") or "Rate"
        existing_rate = hotel_matcher.match_rate_by_name(rate_name, existing_rates)
        existing_seasons = (existing_rate or {}).get("seasons") or []

        season_payloads = []
        season_actions = []
        for season_data in (rate_data or {}).get("seasons") or []:
            season_name = (season_data or {}).get("name") or "Season"
            date_ranges_data = [w for w in (season_data or {}).get("date_ranges") or []
                                 if isinstance(w, dict) and w.get("start") and w.get("end")]
            existing_season = hotel_matcher.match_season_to_existing(season_name, date_ranges_data, existing_seasons)

            room_prices = []
            for rp_data in (season_data or {}).get("room_prices") or []:
                room_name = (rp_data or {}).get("room_name")
                provider_room_code = room_name_to_provider_code.get(room_name)
                if not provider_room_code:
                    continue
                distribution_prices_data = _clip_distributions_to_pax_cap_priced((rp_data or {}).get("distribution_prices") or [])
                room_prices.append(ContractHotelSeasonPricesVO(
                    unitsQuota=_safe_int((rp_data or {}).get("units_quota", 20), fallback=20),
                    unitsOnRequest=_safe_int((rp_data or {}).get("units_on_request", 0), fallback=0),
                    providerRoomCode=provider_room_code,
                    distributionPrices=[ContractRoomDistributionPriceVO(
                        amount=_safe_float(p.get("amount", 0)),
                        adults=_safe_int(p.get("adults", 1), fallback=1),
                        children=_safe_int(p.get("children", 0)),
                    ) for p in distribution_prices_data],
                    basePrice=_safe_float((rp_data or {}).get("base_price", 0)),
                    adultPrices=[_safe_float(p) for p in (rp_data or {}).get("adult_prices") or []],
                    childPrices=[_safe_float(p) for p in (rp_data or {}).get("child_prices") or []],
                ))

            season_meal_plans = [_build_meal_plan_payload(mp) for mp in (season_data or {}).get("meal_plans") or []]

            season_payloads.append(ContractHotelSeasonVO(
                id=(existing_season or {}).get("id"),
                name=season_name,
                dateRanges=[LocalDateRangeVO(start=w["start"], end=w["end"]) for w in date_ranges_data],
                mealPlans=season_meal_plans,
                seasonRoomPrices=room_prices,
                releaseDays=(season_data or {}).get("release_days"),
                minimumStay=_safe_int((season_data or {}).get("minimum_stay", 1), fallback=1),
                maximumStay=(season_data or {}).get("maximum_stay"),
                priceType=_map_price_type((season_data or {}).get("price_type")),
            ))
            season_actions.append({
                "season_name": season_name,
                "action": "update" if existing_season else "create",
                "matched_season_id": (existing_season or {}).get("id"),
            })

        offer_codes = [c for c in (offer_name_to_provider_code.get(n) for n in (rate_data or {}).get("offer_names") or []) if c]
        supplement_codes = [c for c in (supplement_name_to_provider_code.get(n) for n in (rate_data or {}).get("supplement_names") or []) if c]

        stop_sales_payloads = []
        for ss_data in (rate_data or {}).get("stop_sales") or []:
            room_name = (ss_data or {}).get("room_name")
            ss_ranges = [w for w in (ss_data or {}).get("date_ranges") or [] if isinstance(w, dict) and w.get("start") and w.get("end")]
            if not room_name or not ss_ranges:
                continue
            stop_sales_payloads.append(ContractHotelRoomStopSalesVO(
                roomName=room_name,
                stopSales=[LocalDateRangeVO(start=w["start"], end=w["end"]) for w in ss_ranges],
            ))

        booking_windows_data = [w for w in (rate_data or {}).get("booking_windows") or [] if isinstance(w, dict) and w.get("start") and w.get("end")]

        rate_kwargs = dict(
            id=(existing_rate or {}).get("id"),
            name=rate_name,
            bookingWindows=[LocalDateRangeVO(start=w["start"], end=w["end"]) for w in booking_windows_data],
            seasons=season_payloads,
            offers=offer_codes,
            supplements=supplement_codes,
            stopSales=stop_sales_payloads,
            releaseDays=(rate_data or {}).get("release_days"),
            minimumStay=_safe_int((rate_data or {}).get("minimum_stay", 1), fallback=1),
            maximumStay=(rate_data or {}).get("maximum_stay"),
        )

        rate_error = None
        rate_payload = None
        try:
            rate = ContractHotelRateVO(**rate_kwargs)
            rate_payload = rate.dict()
        except ValidationError as e:
            rate_error = str(e)
        except (ValueError, TypeError) as e:
            rate_error = f"Couldn't build rate '{rate_name}' - {e}"

        results.append({
            "rate_payload": rate_payload,
            "rate_error": rate_error,
            "action": "update" if existing_rate else "create",
            "matched_rate_id": (existing_rate or {}).get("id"),
            "season_actions": season_actions,
        })

    return results
