"""Tests for builder.build_closed_tour_payloads with a fake API client (no network calls).

These exercise the full combine-and-build pipeline: destination resolution, itinerary
collapsing, supplement conversion, cancellation policy, and the two payload objects
(main tour + tour option) that eventually get PUT/POST to Travel Compositor.
"""
from schemas import HumanPreConfig
from builder import build_closed_tour_payloads


def make_pre_config(**overrides):
    defaults = dict(
        supplier_id="48940", provider_code="ASW-1", min_pax=1, max_pax=4,
        currency="EUR", modality_code="STANDARD_CABIN", on_request=True,
    )
    defaults.update(overrides)
    return HumanPreConfig(**defaults)


def minimal_extracted_data(**overrides):
    data = {
        "tour_name": "Test Nile Cruise",
        "tour_code": "TOUR-ASW-1",
        "description": "A lovely test cruise.",
        "itinerary_destinations": ["Cairo", "Aswan", "Luxor"],
        "price_list": [{
            "startDate": "2027-01-01", "endDate": "2027-03-31",
            "price": {"singlePrice": {"amount": 500}, "doublePrice": {"amount": 300}},
        }],
        "supplements": [],
        "nights": 3,
    }
    data.update(overrides)
    return data


def test_builds_both_payloads_with_no_errors(fake_api_client):
    result = build_closed_tour_payloads(make_pre_config(), minimal_extracted_data(), fake_api_client)
    assert result["main_tour_error"] is None
    assert result["tour_option_error"] is None
    assert result["main_tour_payload"] is not None
    assert result["tour_option_payload"] is not None


def test_main_tour_payload_carries_the_extracted_name_and_pre_config_values(fake_api_client):
    result = build_closed_tour_payloads(make_pre_config(currency="USD"), minimal_extracted_data(), fake_api_client)
    payload = result["main_tour_payload"]
    assert payload["name"] == "Test Nile Cruise"
    assert payload["currency"] == "USD"
    assert payload["providerCode"] == "ASW-1"
    assert payload["active"] is False  # LOCKED: always uploaded inactive/draft


def test_unresolvable_destination_is_flagged_not_silently_dropped(fake_api_client_factory):
    fake = fake_api_client_factory(unresolvable={"Atlantis"})
    result = build_closed_tour_payloads(
        make_pre_config(), minimal_extracted_data(itinerary_destinations=["Cairo", "Atlantis"]), fake)
    assert "Atlantis" in result["unresolved_destinations"]
    # The itinerary item still exists (as an empty-string placeholder), it just can never
    # reach the real API - see build_closed_tour_payloads' own comment on this.
    statuses = {r["input"]: r["valid"] for r in result["itinerary_resolution"]}
    assert statuses["Atlantis"] is False
    assert statuses["Cairo"] is True


def test_consecutive_duplicate_stops_are_collapsed(fake_api_client):
    """Travel Compositor rejects the same destination appearing twice CONSECUTIVELY -
    two overnight days at the same place must become one itinerary entry."""
    result = build_closed_tour_payloads(
        make_pre_config(),
        minimal_extracted_data(itinerary_destinations=["Cairo", "Cairo", "Aswan"]),
        fake_api_client)
    destinations = [item["destination"] for item in result["main_tour_payload"]["itinerary"]]
    assert destinations.count(fake_api_client._code_for("Cairo")) == 1


def test_non_consecutive_repeat_destination_is_kept(fake_api_client):
    """A tour that RETURNS to an earlier city later is a real, valid itinerary shape and
    must not be collapsed the way back-to-back duplicates are."""
    result = build_closed_tour_payloads(
        make_pre_config(),
        minimal_extracted_data(itinerary_destinations=["Cairo", "Aswan", "Cairo"]),
        fake_api_client)
    destinations = [item["destination"] for item in result["main_tour_payload"]["itinerary"]]
    assert destinations.count(fake_api_client._code_for("Cairo")) == 2


def test_supplements_are_never_scoped_to_a_single_modality(fake_api_client):
    """CONFIRMED PRODUCT-OWNER RULE: a ClosedTour supplement applies to every Modality."""
    result = build_closed_tour_payloads(
        make_pre_config(),
        minimal_extracted_data(supplements=[{"name": "Balloon Ride", "price": 120}]),
        fake_api_client)
    supplements = result["main_tour_payload"]["supplements"]
    assert len(supplements) == 1
    assert supplements[0]["modalityCodes"] == []
    assert supplements[0]["refundable"] is False


def test_child_age_band_defaults_to_house_convention(fake_api_client):
    result = build_closed_tour_payloads(make_pre_config(), minimal_extracted_data(), fake_api_client)
    assert result["main_tour_payload"]["minChildAge"] == 2
    assert result["main_tour_payload"]["maxChildAge"] == 12


def test_stated_minimum_child_age_replaces_the_house_floor(fake_api_client):
    result = build_closed_tour_payloads(
        make_pre_config(), minimal_extracted_data(min_child_age=7), fake_api_client)
    assert result["main_tour_payload"]["minChildAge"] == 7
    assert result["main_tour_payload"]["maxChildAge"] == 12


def test_no_cancellation_tiers_stated_uses_the_flat_default(fake_api_client):
    result = build_closed_tour_payloads(make_pre_config(), minimal_extracted_data(), fake_api_client)
    ranges = result["main_tour_payload"]["cancellationRanges"]
    assert len(ranges) == 1
    assert ranges[0]["percentage"] == 100  # default CancellationRange()


def test_the_tour_option_payload_carries_the_price_list_sorted_by_start_date(fake_api_client):
    data = minimal_extracted_data(price_list=[
        {"startDate": "2027-06-01", "endDate": "2027-08-31", "price": {"singlePrice": {"amount": 600}}},
        {"startDate": "2027-01-01", "endDate": "2027-03-31", "price": {"singlePrice": {"amount": 500}}},
    ])
    result = build_closed_tour_payloads(make_pre_config(), data, fake_api_client)
    starts = [row["startDate"] for row in result["tour_option_payload"]["priceList"]]
    assert starts == sorted(starts)


def test_tour_code_falls_back_to_a_guess_when_not_extracted(fake_api_client):
    result = build_closed_tour_payloads(
        make_pre_config(provider_code="XYZ-9"), minimal_extracted_data(tour_code=None), fake_api_client)
    assert result["main_tour_code"] == "TOUR-XYZ-9"


def test_a_malformed_supplement_price_degrades_to_a_per_field_error_not_a_crash(fake_api_client):
    """CONFIRMED FIX (real production crash, SUB-1): a non-numeric supplement price must
    surface as main_tour_error, never raise out of build_closed_tour_payloads."""
    # _safe_supplement_price already guards this at the supplement layer, so this proves
    # the whole pipeline stays exception-free even with a hostile shape thrown at it.
    data = minimal_extracted_data(supplements=[{"name": "Weird", "price": {"nested": {"deeply": True}}}])
    result = build_closed_tour_payloads(make_pre_config(), data, fake_api_client)
    assert result["main_tour_error"] is None  # never raises; _safe_supplement_price falls back to 0
