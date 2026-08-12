"""Tests for builder.build_ticket_payloads with a fake API client (no network calls).

manual_latitude/manual_longitude are supplied in every fixture so geolocation resolves
without ever calling out to the real geocoder (geocode() in geocoding_client.py) - see
build_ticket_payloads' own geolocation branch for why a manual override skips it entirely.
"""
from schemas import TicketHumanPreConfig
from builder import build_ticket_payloads, build_ticket_supplement_vos


def make_pre_config(**overrides):
    defaults = dict(
        supplier_id="48940", ticket_code="JAP-T1", currency="EUR",
        modality_code="Standard", on_request=False,
    )
    defaults.update(overrides)
    return TicketHumanPreConfig(**defaults)


def minimal_ticket_data(**overrides):
    data = {
        "ticket_name": "Tokyo City Tour",
        "description": "A test excursion.",
        "city": "Tokyo",
        "manual_latitude": 35.6895,
        "manual_longitude": 139.6917,
        "base_adult_price": 50,
        "price_type": "DISTRIBUTION",
    }
    data.update(overrides)
    return data


def test_builds_both_payloads_with_no_errors(fake_api_client):
    result = build_ticket_payloads(make_pre_config(), minimal_ticket_data(), fake_api_client)
    assert result["main_ticket_error"] is None
    assert result["ticket_option_error"] is None
    assert result["main_ticket_payload"] is not None
    assert result["ticket_option_payload"] is not None


def test_manual_geolocation_is_used_directly_no_geocoder_call(fake_api_client):
    result = build_ticket_payloads(make_pre_config(), minimal_ticket_data(), fake_api_client)
    assert result["geolocation_resolved"] is True
    assert result["geolocation_latitude"] == 35.6895
    assert result["geolocation_longitude"] == 139.6917
    assert result["geolocation_source"] == "manual override"
    # And it must not have gone looking for a transfer zone either, since manual coords win.
    assert not any(c[0] == "resolve_transfer_zone_geolocation" for c in fake_api_client.calls)


def test_ticket_code_is_prefixed_for_the_guess(fake_api_client):
    result = build_ticket_payloads(make_pre_config(ticket_code="JAP-T1"), minimal_ticket_data(), fake_api_client)
    assert result["main_ticket_code"] == "TICKET-JAP-T1"


def test_tickets_never_carry_supplements_but_names_them_when_dropped(fake_api_client):
    """CONFIRMED PRODUCT-OWNER RULE: a Ticket has no supplements - every extra is its own
    Modality instead. Anything still sitting in extracted data must be dropped, not
    silently discarded - it is reported in ignored_ticket_supplements."""
    data = minimal_ticket_data(supplements=[{"name": "Fast pass", "price": 20}])
    result = build_ticket_payloads(make_pre_config(), data, fake_api_client)
    assert result["ticket_option_payload"]["supplements"] == []
    assert "Fast pass" in result["ignored_ticket_supplements"]


def test_distribution_pricing_zeroes_the_other_two_modes(fake_api_client):
    data = minimal_ticket_data(price_type="DISTRIBUTION", base_service_price=99,
                               occupancy_prices=[{"occupancy": 2, "amount": 40}])
    result = build_ticket_payloads(make_pre_config(), data, fake_api_client)
    option = result["ticket_option_payload"]
    assert option["priceType"] == "DISTRIBUTION"
    assert option["baseServicePrice"] == 0.0
    assert option["occupancyPrices"] == []


def test_service_pricing_zeroes_the_other_two_modes(fake_api_client):
    data = minimal_ticket_data(price_type="SERVICE", base_service_price=150,
                               occupancy_prices=[{"occupancy": 2, "amount": 40}])
    result = build_ticket_payloads(make_pre_config(), data, fake_api_client)
    option = result["ticket_option_payload"]
    assert option["priceType"] == "SERVICE"
    assert option["baseServicePrice"] == 150.0
    assert option["occupancyPrices"] == []
    # baseAdultPrice is required non-zero on the wire regardless of mode
    assert option["baseAdultPrice"] == 1.0


def test_occupancy_pricing_zeroes_the_other_two_modes(fake_api_client):
    data = minimal_ticket_data(price_type="OCCUPANCY",
                               occupancy_prices=[{"occupancy": 2, "amount": 40}])
    result = build_ticket_payloads(make_pre_config(), data, fake_api_client)
    option = result["ticket_option_payload"]
    assert option["priceType"] == "OCCUPANCY"
    assert option["occupancyPrices"] == [{"occupancy": 2, "amount": 40.0}]
    assert option["baseServicePrice"] == 0.0


def test_occupancy_prices_above_9_pax_are_dropped(fake_api_client):
    """CONFIRMED REAL SYSTEM LIMIT (product owner): Travel Compositor can't book more than 9
    people on any product - the same rule already enforced for Transfer/Transport via
    _MAX_OCCUPANCY_PAX. A source rate sheet with a "9-14 pax" style column used to sail
    straight through into occupancyPrices with entries nobody could ever book; the fix must
    silently drop anything above 9 rather than publish it."""
    data = minimal_ticket_data(price_type="OCCUPANCY", occupancy_prices=[
        {"occupancy": 2, "amount": 40},
        {"occupancy": 9, "amount": 15},
        {"occupancy": 10, "amount": 14},
        {"occupancy": 14, "amount": 12},
    ])
    result = build_ticket_payloads(make_pre_config(), data, fake_api_client)
    option = result["ticket_option_payload"]
    assert option["occupancyPrices"] == [
        {"occupancy": 2, "amount": 40.0},
        {"occupancy": 9, "amount": 15.0},
    ]


def test_occupancy_prices_are_capped_at_this_tickets_own_max_passengers(fake_api_client):
    """CONFIRMED REAL BUG (production failure, real API response): "Number of passengers in
    occupancy is greater than max passengers allowed in the contract". The flat 9-pax system
    limit isn't the real ceiling - THIS TICKET'S max_passengers is, and it can be set below 9
    (Step 3's Max Passengers selector goes down to 2). An occupancy row above max_passengers
    must be dropped even when it's still <= 9, or Travel Compositor rejects the whole option."""
    data = minimal_ticket_data(price_type="OCCUPANCY", occupancy_prices=[
        {"occupancy": 2, "amount": 40},
        {"occupancy": 4, "amount": 25},
        {"occupancy": 6, "amount": 18},
        {"occupancy": 9, "amount": 15},
    ])
    result = build_ticket_payloads(make_pre_config(max_passengers=4), data, fake_api_client)
    option = result["ticket_option_payload"]
    assert option["occupancyPrices"] == [
        {"occupancy": 2, "amount": 40.0},
        {"occupancy": 4, "amount": 25.0},
    ]
    assert option["maxPassengers"] == 4


def test_child_age_defaults_to_2_and_12(fake_api_client):
    result = build_ticket_payloads(make_pre_config(), minimal_ticket_data(), fake_api_client)
    option = result["ticket_option_payload"]
    assert option["childAgeMin"] == 2
    assert option["childAgeMax"] == 12


def test_modality_code_with_a_slash_is_rejected_by_the_schema_before_building(fake_api_client):
    """This is a pydantic validator on the pre_config itself (schemas.py), not builder.py -
    confirming it fires means a bad code can never even reach build_ticket_payloads."""
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        make_pre_config(modality_code="BAD/CODE")


# CORRECTED 2026-08-12 (product owner): "Main Ticket information has no supplement, Modality of
# a Ticket has their own supplement." The main Ticket record still has none (see the
# ignored_ticket_supplements test above, unchanged), but each Modality now carries its own
# dated modality_supplements - a seasonal price row or a holiday surcharge, as opposed to a
# genuinely different product a customer chooses (still its own Modality, unchanged).

def test_build_ticket_supplement_vos_converts_dated_entries():
    result = build_ticket_supplement_vos([
        {"name": "High Season", "adult_price_supplement": 15, "children_price_supplement": 5,
         "infant_price_supplement": 0, "start_date": "2027-01-01", "end_date": "2027-02-28"},
    ])
    assert len(result) == 1
    vo = result[0]
    assert vo.adultPriceSupplement == 15.0
    assert vo.childrenPriceSupplement == 5.0
    assert vo.startDate == "2027-01-01"
    assert vo.endDate == "2027-02-28"
    assert vo.translations["EN"].name == "High Season"


def test_build_ticket_supplement_vos_drops_entries_missing_either_date():
    """TicketSupplementVO has no undated fallback (unlike ClosedTour's SupplementVO) - an
    entry that can't be told apart from a permanent price rise must not publish."""
    result = build_ticket_supplement_vos([
        {"name": "No start", "adult_price_supplement": 10, "end_date": "2027-02-28"},
        {"name": "No end", "adult_price_supplement": 10, "start_date": "2027-01-01"},
        {"name": "Fully dated", "adult_price_supplement": 10, "start_date": "2027-01-01", "end_date": "2027-02-28"},
    ])
    assert len(result) == 1
    assert result[0].translations["EN"].name == "Fully dated"


def test_ticket_modality_supplements_reach_the_real_payload(fake_api_client):
    """The whole point: a dated seasonal/holiday supplement extracted onto THIS Modality must
    actually reach ContractTicketModalityVO.supplements on the wire, not be dropped like the
    old (too-broad) "Tickets have no supplements" rule used to force."""
    data = minimal_ticket_data(modality_supplements=[
        {"name": "Tet Holiday Surcharge", "adult_price_supplement": 47, "children_price_supplement": 47,
         "infant_price_supplement": 0, "start_date": "2027-02-05", "end_date": "2027-02-09"},
    ])
    result = build_ticket_payloads(make_pre_config(), data, fake_api_client)
    option = result["ticket_option_payload"]
    assert len(option["supplements"]) == 1
    supp = option["supplements"][0]
    assert supp["adultPriceSupplement"] == 47.0
    assert supp["startDate"] == "2027-02-05"
    assert supp["endDate"] == "2027-02-09"
    assert supp["translations"]["EN"]["name"] == "Tet Holiday Surcharge"


def test_a_stray_none_string_in_time_tables_is_filtered_not_sent_to_the_api(fake_api_client):
    """CONFIRMED FIX (real production crash): a blank data_editor row's None getting
    str()'d into the literal text "None" used to reach LocalTime deserialization server-side
    and blow up with a raw DateTimeParseException."""
    data = minimal_ticket_data(time_tables=["09:00", "None", "", "nan", "14:30"])
    result = build_ticket_payloads(make_pre_config(), data, fake_api_client)
    assert result["ticket_option_payload"]["timeTables"] == ["09:00", "14:30"]


def test_has_real_pricing_reflects_whether_any_base_price_was_actually_entered(fake_api_client):
    with_price = build_ticket_payloads(make_pre_config(), minimal_ticket_data(base_adult_price=50), fake_api_client)
    without_price = build_ticket_payloads(
        make_pre_config(), minimal_ticket_data(base_adult_price=0), fake_api_client)
    assert with_price["has_real_pricing"] is True
    assert without_price["has_real_pricing"] is False
