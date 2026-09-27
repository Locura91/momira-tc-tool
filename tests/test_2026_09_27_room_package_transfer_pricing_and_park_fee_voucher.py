"""Regression tests for a real product-owner design conversation (2026-09-27), grounded in a real
Our Jungle Resorts / Khao Sok contract that needed Room + mandatory Package + National Park Fee +
Transfer all reconciled into one ClosedTour Modality price - see
claude/closedtour-multi-factor-pricing-design-questions-2026-09-27.md for the full discussion.

Two confirmed pieces are covered here:

1. `builder.build_room_package_transfer_occupancy_prices` - the core math: room rate (by season,
   per night, divided by occupancy) + package price (per adult/child) + round-trip transfer
   (one-way rate x2, divided by occupancy). Verbatim confirmations this pins down:
   - "a closedtour must understand: Room costs (land-based accommodation charged separately per
     room type/season) the different seasons of this room prices and add them always to the
     modality."
   - "the 4090 is one way price and must be used double for return price. transfer total price
     must be devided be the total pax price."
   - Real worked numbers from the contract: Treehouse Double room at 3,780 THB/night, a 2-night
     stay, a 6,650 THB/adult package, and a 4,090 THB one-way Phuket transfer.

2. National Park Fee is deliberately EXCLUDED from the price and instead appended to the
   ClosedTour's own voucherRemarks as an informational note - verbatim: "It includes room +
   package + transfer but the park fee must be stated it is paid on field when client is there.
   This information must be added to the voucher remarks as well." This mirrors the already-
   shipped Transfer `location_notes` mechanism (a location-conditional cost that can't be safely
   auto-applied to price becomes an informational voucher note instead).
"""
from schemas import HumanPreConfig
from builder import build_room_package_transfer_occupancy_prices, build_closed_tour_payloads


# ======================================================================
# build_room_package_transfer_occupancy_prices
# ======================================================================
def test_worked_example_from_the_real_contract_double_occupancy():
    # Treehouse Double: 3,780 THB/night, 2-night package, room max_occupancy=2.
    # Package: 6,650 THB/adult. Transfer: 4,090 THB one-way (Phuket), round trip = 8,180.
    # Double price = (3780*2/2) + 6650 + (8180/2) = 3780 + 6650 + 4090 = 14520
    prices, notes = build_room_package_transfer_occupancy_prices(
        nights=2, room_rate_per_night=3780, room_max_occupancy=2,
        package_price_per_adult=6650, transfer_one_way_rate=4090)
    assert prices["double_price"] == 14520.0
    assert notes == []


def test_worked_example_single_occupancy_same_room():
    # Single price = (3780*2/1) + 6650 + (8180/1) = 7560 + 6650 + 8180 = 22390
    prices, notes = build_room_package_transfer_occupancy_prices(
        nights=2, room_rate_per_night=3780, room_max_occupancy=2,
        package_price_per_adult=6650, transfer_one_way_rate=4090)
    assert prices["single_price"] == 22390.0


def test_only_occupancies_the_room_actually_sells_are_returned():
    # A "Double" room (max_occupancy=2) never gets an invented triple/quadruple price.
    prices, notes = build_room_package_transfer_occupancy_prices(
        nights=2, room_rate_per_night=3780, room_max_occupancy=2,
        package_price_per_adult=6650, transfer_one_way_rate=4090)
    assert set(prices.keys()) == {"single_price", "double_price"}


def test_triple_room_gets_all_three_tiers():
    prices, notes = build_room_package_transfer_occupancy_prices(
        nights=2, room_rate_per_night=4160, room_max_occupancy=3,
        package_price_per_adult=6650, transfer_one_way_rate=4090)
    assert set(prices.keys()) == {"single_price", "double_price", "triple_price"}


def test_max_occupancy_above_four_is_capped_and_flagged():
    # The "2 Storey" Treehouse category sleeps 6 - Travel Compositor's schema only has 4 slots.
    prices, notes = build_room_package_transfer_occupancy_prices(
        nights=2, room_rate_per_night=4910, room_max_occupancy=6,
        package_price_per_adult=6650, transfer_one_way_rate=4090)
    assert set(prices.keys()) == {"single_price", "double_price", "triple_price", "quadruple_price"}
    assert any("4" in n and "6" in n for n in notes)


def test_transfer_defaults_to_zero_when_not_given():
    # A tour with no transfer component at all (transfer not yet confirmed) still prices
    # correctly on room + package alone.
    prices, notes = build_room_package_transfer_occupancy_prices(
        nights=2, room_rate_per_night=3780, room_max_occupancy=2, package_price_per_adult=6650)
    assert prices["double_price"] == 3780.0 + 6650.0


def test_different_one_way_and_return_transfer_rates():
    # Arrival and departure legs from different cities/rates - not simply doubled.
    prices, notes = build_room_package_transfer_occupancy_prices(
        nights=2, room_rate_per_night=3780, room_max_occupancy=2, package_price_per_adult=6650,
        transfer_one_way_rate=4090, transfer_one_way_rate_return=3230)
    # round trip total = 4090 + 3230 = 7320, /2 = 3660
    assert prices["double_price"] == 3780.0 + 6650.0 + 3660.0


def test_child_price_computed_alongside_adult_price():
    prices, notes = build_room_package_transfer_occupancy_prices(
        nights=2, room_rate_per_night=3780, room_max_occupancy=2,
        package_price_per_adult=6650, package_price_per_child=5520, transfer_one_way_rate=4090)
    assert prices["double_child_price"] == 3780.0 + 5520.0 + 4090.0
    assert prices["double_price"] == 3780.0 + 6650.0 + 4090.0


def test_missing_room_rate_returns_empty_with_a_note():
    prices, notes = build_room_package_transfer_occupancy_prices(
        nights=2, room_rate_per_night=0, room_max_occupancy=2, package_price_per_adult=6650)
    assert prices == {}
    assert notes


def test_missing_nights_returns_empty_with_a_note():
    prices, notes = build_room_package_transfer_occupancy_prices(
        nights=0, room_rate_per_night=3780, room_max_occupancy=2, package_price_per_adult=6650)
    assert prices == {}
    assert notes


# ======================================================================
# ClosedTour park_fee_notes -> voucherRemarks wiring
# ======================================================================
def make_pre_config(**overrides):
    defaults = dict(
        supplier_id="48940", provider_code="ASW-1", min_pax=1, max_pax=4,
        currency="THB", modality_code="STANDARD_CABIN", on_request=True,
    )
    defaults.update(overrides)
    return HumanPreConfig(**defaults)


def minimal_extracted_data(**overrides):
    data = {
        "tour_name": "Our Jungle Resorts",
        "tour_code": "TOUR-OJR-1",
        "description": "A lovely test resort stay.",
        "itinerary_destinations": ["Phuket", "Khao Sok"],
        "price_list": [{
            "startDate": "2026-11-01", "endDate": "2027-10-31",
            "price": {"singlePrice": {"amount": 22390}, "doublePrice": {"amount": 14520}},
        }],
        "supplements": [],
        "nights": 2,
    }
    data.update(overrides)
    return data


def test_park_fee_notes_lands_in_voucher_remarks(fake_api_client):
    park_fee_text = ("National Park entrance fees of 578 THB per adult and 289 THB per child are "
                      "payable locally, on-site, at the time of your visit - not included in the "
                      "price above.")
    result = build_closed_tour_payloads(
        make_pre_config(), minimal_extracted_data(park_fee_notes=park_fee_text), fake_api_client)
    voucher = result["main_tour_payload"]["datasheets"]["EN"]["voucherRemarks"]
    assert park_fee_text in voucher


def test_no_park_fee_notes_means_nothing_extra_added(fake_api_client):
    baseline = build_closed_tour_payloads(
        make_pre_config(), minimal_extracted_data(), fake_api_client)
    with_empty = build_closed_tour_payloads(
        make_pre_config(), minimal_extracted_data(park_fee_notes=""), fake_api_client)
    baseline_voucher = baseline["main_tour_payload"]["datasheets"]["EN"]["voucherRemarks"]
    empty_voucher = with_empty["main_tour_payload"]["datasheets"]["EN"]["voucherRemarks"]
    assert baseline_voucher == empty_voucher
    assert "National Park" not in baseline_voucher


def test_park_fee_notes_never_affects_price(fake_api_client):
    # The whole point of decision #4: this text is informational only, never a price component.
    park_fee_text = "National Park fees payable locally."
    without = build_closed_tour_payloads(
        make_pre_config(), minimal_extracted_data(), fake_api_client)
    withit = build_closed_tour_payloads(
        make_pre_config(), minimal_extracted_data(park_fee_notes=park_fee_text), fake_api_client)
    assert without["tour_option_payload"] == withit["tour_option_payload"]
