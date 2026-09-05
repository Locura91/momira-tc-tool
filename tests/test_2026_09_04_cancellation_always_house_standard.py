"""End-to-end regression tests for the standing rule (product owner, 2026-09-04):

    "Cancellation like: More than 48 hours before the tour: no fee (up to this point). Within 48
    hours before the tour: 50% fee. No show: no refund. --> Do not show as remark, as our
    internal cancellation with 30 days or prior is better for our Momira company."
    Confirmed via follow-up: always just the flat 30-day policy, as a standing rule for all
    future document-driven uploads (not just this one product).

Unlike the shared-helper-level tests (test_2026_08_24_start_date_and_cancellation_floor.py,
test_2026_09_03_cancellation_voucher_text_uses_floored_tiers.py), these exercise each of the 5
product builders' own full build_*_payloads/build_*_payload entry points with realistic,
aggressive supplier-stated cancellation data (a graduated schedule with a too-generous tier, a
stricter tier, AND free-text prose attached) and assert the PUBLISHED output - both the
structured cancellation field (where one exists) and the customer-facing voucher/description text
- always ends up as Momira's flat 30-day/100%-refund house standard, never anything the document
itself stated. See builder.py's _MIN_FULL_REFUND_NOTICE_DAYS module comment for the full history:
the actual mechanism is that every builder call site now passes None into
_cancellation_ranges_from_tiers/_cancellation_voucher_text instead of the document's own
extracted values, regardless of what those values are - these tests confirm that holds all the
way through to the published payload, for all 5 product types, not just at the helper level.
"""
from schemas import (
    HumanPreConfig, TicketHumanPreConfig, TransferHumanPreConfig, TransportHumanPreConfig,
)
from builder import (
    build_closed_tour_payloads, build_ticket_payloads, build_transfer_payload,
    build_transport_payloads, build_hotel_contract_payload,
)

# A real-world-shaped aggressive supplier policy: a too-generous short-notice full refund tier,
# a stricter partial-refund tier, and free text that would leak the supplier's own wording if
# anything here still read the document's own cancellation data.
AGGRESSIVE_TIERS = [
    {"days": 2, "fee_percentage": 0},      # "more than 48 hours before: no fee"
    {"days": 0, "fee_percentage": 50},     # "within 48 hours: 50% fee"
]
AGGRESSIVE_TEXT = (
    "More than 48 hours before the tour: no fee. Within 48 hours: 50% fee. No show: no refund."
)


def _assert_never_leaks_supplier_wording(voucher_text):
    assert voucher_text is not None
    assert "48 hour" not in voucher_text
    assert "50%" not in voucher_text
    assert "no show" not in voucher_text.lower()
    assert "30 days" in voucher_text


# ======================================================================
# ClosedTour
# ======================================================================
def test_closed_tour_always_publishes_the_flat_30_day_standard(fake_api_client):
    pre_config = HumanPreConfig(
        supplier_id="48940", provider_code="ASW-1", min_pax=1, max_pax=4,
        currency="EUR", modality_code="STANDARD_CABIN", on_request=True,
    )
    extracted = {
        "tour_name": "Test Nile Cruise", "tour_code": "TOUR-ASW-1",
        "description": "A lovely test cruise.",
        "itinerary_destinations": ["Cairo", "Aswan", "Luxor"],
        "price_list": [{
            "startDate": "2027-01-01", "endDate": "2027-03-31",
            "price": {"singlePrice": {"amount": 500}, "doublePrice": {"amount": 300}},
        }],
        "supplements": [], "nights": 3,
        "cancellation_policy_tiers": AGGRESSIVE_TIERS,
        "cancellation_policy_text": AGGRESSIVE_TEXT,
    }
    result = build_closed_tour_payloads(pre_config, extracted, fake_api_client)
    assert result["main_tour_error"] is None
    ranges = result["main_tour_payload"]["cancellationRanges"]
    assert ranges == [{"days": 30, "percentage": 100.0}]
    voucher = result["main_tour_payload"]["datasheets"]["EN"]["voucherRemarks"]
    _assert_never_leaks_supplier_wording(voucher)


# ======================================================================
# Ticket
# ======================================================================
def test_ticket_always_publishes_the_flat_30_day_standard(fake_api_client):
    pre_config = TicketHumanPreConfig(
        supplier_id="48940", ticket_code="JAP-T1", currency="EUR",
        modality_code="Standard", on_request=False,
    )
    extracted = {
        "ticket_name": "Tokyo City Tour", "description": "A test excursion.", "city": "Tokyo",
        "manual_latitude": 35.6895, "manual_longitude": 139.6917,
        "base_adult_price": 50, "price_type": "DISTRIBUTION",
        "cancellation_policy_tiers": AGGRESSIVE_TIERS,
        "cancellation_policy_text": AGGRESSIVE_TEXT,
        "voucher_remarks": "",
    }
    result = build_ticket_payloads(pre_config, extracted, fake_api_client)
    assert result["main_ticket_error"] is None
    ranges = result["main_ticket_payload"]["cancellationRanges"]
    assert ranges == [{"cancellationDays": 30, "cancellationPercentage": 100.0}]
    voucher = result["main_ticket_payload"]["datasheets"]["EN"]["voucherRemarks"]
    _assert_never_leaks_supplier_wording(voucher)


# ======================================================================
# Transfer (no structured cancellation field - text only)
# ======================================================================
def test_transfer_always_publishes_the_flat_30_day_standard_text(fake_api_client):
    pre_config = TransferHumanPreConfig(supplier_id="50696", currency="EUR")
    extracted = {
        "departure_name": "Hurghada Airport", "arrival_name": "Hurghada Hotel Zone",
        "service_name": "Airport Transfer",
        "manual_departure_latitude": 27.18, "manual_departure_longitude": 33.80,
        "manual_arrival_latitude": 27.25, "manual_arrival_longitude": 33.83,
        "occupancy_price_tiers": [{"occupancy": 1, "price": 20}],
        "cancellation_policy_tiers": AGGRESSIVE_TIERS,
        "cancellation_policy_text": AGGRESSIVE_TEXT,
    }
    result = build_transfer_payload(pre_config, extracted, fake_api_client)
    assert result["transfer_error"] is None
    voucher = result["transfer_payload"]["datasheets"]["EN"]["voucherRemarks"]
    _assert_never_leaks_supplier_wording(voucher)


# ======================================================================
# Transport (structured field + text embedded in description)
# ======================================================================
def test_transport_always_publishes_the_flat_30_day_standard(fake_api_client):
    pre_config = TransportHumanPreConfig(supplier_id="50696", currency="EUR")
    extracted = {
        "departure_name": "Aswan", "arrival_name": "Luxor",
        "service_name": "Private Transport",
        "occupancy_brackets": [{"min_occupancy": 1, "max_occupancy": 4, "price": 100}],
        "cancellation_policy_tiers": AGGRESSIVE_TIERS,
        "cancellation_policy_text": AGGRESSIVE_TEXT,
    }
    result = build_transport_payloads(pre_config, extracted, fake_api_client)
    assert result["transport_error"] is None
    ranges = result["transport_payload"]["cancellationRanges"]
    assert len(ranges) == 1
    assert ranges[0]["days"] == 30
    assert ranges[0]["percentage"] == 100.0
    description = result["transport_payload"]["datasheets"]["EN"]["description"]
    _assert_never_leaks_supplier_wording(description)


# ======================================================================
# Hotel (no structured field - voucherRemarks only, per-language list shape)
# ======================================================================
def test_hotel_always_publishes_the_flat_30_day_standard_text():
    pre_config = HumanPreConfig(
        supplier_id="48940", provider_code="CAI-H1", min_pax=1, max_pax=4,
        currency="EUR", modality_code="STANDARD",
    )
    extracted = {
        "hotelname": "Test Hotel",
        "rooms": [{"name": "Deluxe Room", "distributions": [{"adults": 2, "children": 0}]}],
        "cancellation_policy_tiers": AGGRESSIVE_TIERS,
        "cancellation_policy_text": AGGRESSIVE_TEXT,
    }
    result = build_hotel_contract_payload(pre_config, extracted, existing_hotel_snapshot=None)
    assert result["hotel_error"] is None
    voucher_remarks = result["hotel_payload"]["voucherRemarks"]
    # Hotel's voucherRemarks is a List[TranslationVO]-shaped list ({language, description} pairs),
    # not a single string.
    assert isinstance(voucher_remarks, list) and voucher_remarks
    voucher = voucher_remarks[0]["description"]
    _assert_never_leaks_supplier_wording(voucher)
