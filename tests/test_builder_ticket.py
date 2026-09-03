"""Tests for builder.build_ticket_payloads with a fake API client (no network calls).

manual_latitude/manual_longitude are supplied in every fixture so geolocation resolves
without ever calling out to the real geocoder (geocode() in geocoding_client.py) - see
build_ticket_payloads' own geolocation branch for why a manual override skips it entirely.
"""
from schemas import TicketHumanPreConfig
from builder import (
    build_ticket_payloads, build_ticket_supplement_vos, coerce_ticket_occupancy_prices_shape,
    resolve_ticket_child_price_ratio,
)


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


def test_occupancy_pricing_adds_a_separate_child_row_with_age_range_when_children_allowed(fake_api_client):
    """CONFIRMED REAL SHAPE (live GET response, 2026-08-13): a child price is its own
    {"occupancy", "amount", "ageRange"} entry in the SAME occupancyPrices list as the adult
    row for that headcount - not a field tacked onto the adult row (that was an earlier, wrong
    guess that Travel Compositor silently ignored)."""
    data = minimal_ticket_data(price_type="OCCUPANCY", disallow_children=False,
                               child_age_min=2, child_age_max=12,
                               occupancy_prices=[{"occupancy": 2, "amount": 40, "child_amount": 20}])
    result = build_ticket_payloads(make_pre_config(), data, fake_api_client)
    option = result["ticket_option_payload"]
    assert option["occupancyPrices"] == [
        {"occupancy": 2, "amount": 40.0},
        {"occupancy": 2, "amount": 20.0, "ageRange": {"min": 2, "max": 12}},
    ]


def test_occupancy_pricing_uses_the_resolved_child_age_band_in_age_range(fake_api_client):
    data = minimal_ticket_data(price_type="OCCUPANCY", disallow_children=False,
                               child_age_min=7, child_age_max=None,
                               occupancy_prices=[{"occupancy": 2, "amount": 40, "child_amount": 20}])
    result = build_ticket_payloads(make_pre_config(), data, fake_api_client)
    option = result["ticket_option_payload"]
    # resolve_child_age_band: a stated floor (7) with no stated ceiling keeps the house
    # ceiling (12) rather than collapsing to a zero-width 7-7 band.
    assert option["occupancyPrices"][1]["ageRange"] == {"min": 7, "max": 12}


def test_occupancy_pricing_omits_the_child_row_when_children_disallowed(fake_api_client):
    """CONFIRMED PRODUCT-OWNER RULE: the child column only applies when this Ticket actually
    allows children - a disallow_children=True Ticket has no child rate to publish, even if a
    stale child_amount is still sitting in the working data from before it was toggled off."""
    data = minimal_ticket_data(price_type="OCCUPANCY", disallow_children=True,
                               occupancy_prices=[{"occupancy": 2, "amount": 40, "child_amount": 20}])
    result = build_ticket_payloads(make_pre_config(), data, fake_api_client)
    option = result["ticket_option_payload"]
    assert option["occupancyPrices"] == [{"occupancy": 2, "amount": 40.0}]


def test_occupancy_pricing_omits_the_child_row_when_the_row_never_had_one(fake_api_client):
    data = minimal_ticket_data(price_type="OCCUPANCY", disallow_children=False,
                               occupancy_prices=[{"occupancy": 2, "amount": 40}])
    result = build_ticket_payloads(make_pre_config(), data, fake_api_client)
    option = result["ticket_option_payload"]
    assert option["occupancyPrices"] == [{"occupancy": 2, "amount": 40.0}]


def test_occupancy_pricing_multiple_rows_interleave_adult_and_child_entries(fake_api_client):
    data = minimal_ticket_data(price_type="OCCUPANCY", disallow_children=False, occupancy_prices=[
        {"occupancy": 1, "amount": 202, "child_amount": 202},
        {"occupancy": 2, "amount": 110, "child_amount": 110},
    ])
    result = build_ticket_payloads(make_pre_config(), data, fake_api_client)
    option = result["ticket_option_payload"]
    assert option["occupancyPrices"] == [
        {"occupancy": 1, "amount": 202.0},
        {"occupancy": 1, "amount": 202.0, "ageRange": {"min": 2, "max": 12}},
        {"occupancy": 2, "amount": 110.0},
        {"occupancy": 2, "amount": 110.0, "ageRange": {"min": 2, "max": 12}},
    ]


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


def test_child_age_minimum_with_no_stated_ceiling_does_not_invert(fake_api_client):
    """CONFIRMED REAL BUG: a document saying "children accepted from age 14" with no stated
    ceiling used to publish childAgeMin=14/childAgeMax=12 - an inverted band that bills every
    child as an infant. ClosedTour already repairs this via resolve_child_age_band; Ticket
    must behave the same way for the identical kind of source statement."""
    data = minimal_ticket_data(child_age_min=14)
    result = build_ticket_payloads(make_pre_config(), data, fake_api_client)
    option = result["ticket_option_payload"]
    assert option["childAgeMin"] == 14
    assert option["childAgeMax"] >= 14


def test_modality_code_with_a_slash_has_the_slash_silently_stripped_by_the_schema(fake_api_client):
    """This is a pydantic validator on the pre_config itself (schemas.py), not builder.py.

    UPDATE (2026-09-03, real product-owner report): this used to hard-reject with a
    ValidationError, forcing a manual retype - but a human-edited Modality Code (not just an
    AI/UI default) could carry a stray "/" straight from a supplier's excursion name, and
    hard-rejecting just repeated the same error the human had already been forced to fix once.
    "The modality code can include () or ! or - that is not a problem any more" (product owner) -
    only "/" and "\\" actually break the URL this code is embedded into, so those two are now
    silently stripped instead of rejecting the whole value. See
    test_2026_09_03_ticket_modality_code_slash_fix_and_batch_recovery.py.
    """
    pre_config = make_pre_config(modality_code="BAD/CODE")
    assert pre_config.modality_code == "BADCODE"


# CONFIRMED (product owner, 2026-08-22): code and client-facing name are NOT the same thing.
# A supplier's own per-service reference code (e.g. "WT1" from a "Tour Code" column) belongs
# in the CODE, appended to the standard short-code - never in the NAME the client sees.
def test_modality_code_and_name_are_independent_when_a_supplier_code_is_appended(fake_api_client):
    pre_config = make_pre_config(modality_code="STANDARD_WT1", modality_name="Standard Private")
    result = build_ticket_payloads(pre_config, minimal_ticket_data(), fake_api_client)
    assert result["ticket_option_error"] is None
    assert result["ticket_option_payload"]["code"] == "STANDARD_WT1"
    # The client-facing name lives in remarks[EN].name - must stay the plain descriptive name,
    # never the supplier's reference code.
    assert result["ticket_option_payload"]["remarks"]["EN"]["name"] == "Standard Private"


def test_modality_name_falls_back_to_modality_code_when_not_given(fake_api_client):
    """The normal case (no per-service supplier code on the document) - behaviour must be
    identical to before this code/name split existed."""
    pre_config = make_pre_config(modality_code="Standard")
    result = build_ticket_payloads(pre_config, minimal_ticket_data(), fake_api_client)
    assert result["ticket_option_payload"]["code"] == "Standard"
    assert result["ticket_option_payload"]["remarks"]["EN"]["name"] == "Standard"


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
    and blow up with a raw DateTimeParseException.

    UPDATED (product owner, 2026-09-03): "If mentioned a start time, just use the earliest time
    of all mentioned and just one" - a Ticket only ever publishes ONE start time now (see
    test_2026_09_03_ticket_start_time_range_and_single_earliest.py), so this keeps only the
    earliest of the genuinely-valid entries once the "None"/blank/"nan" junk is filtered out.
    """
    data = minimal_ticket_data(time_tables=["09:00", "None", "", "nan", "14:30"])
    result = build_ticket_payloads(make_pre_config(), data, fake_api_client)
    assert result["ticket_option_payload"]["timeTables"] == ["09:00"]


def test_has_real_pricing_reflects_whether_any_base_price_was_actually_entered(fake_api_client):
    with_price = build_ticket_payloads(make_pre_config(), minimal_ticket_data(base_adult_price=50), fake_api_client)
    without_price = build_ticket_payloads(
        make_pre_config(), minimal_ticket_data(base_adult_price=0), fake_api_client)
    assert with_price["has_real_pricing"] is True
    assert without_price["has_real_pricing"] is False


# ---------------------------------------------------------------------------
# coerce_ticket_occupancy_prices_shape - the "Tell AI what to fix" safety net
# for a Ticket Modality's occupancy_prices (see apply_clarify_changes in app.py
# and the CONFIRMED REAL RISK docstring on this function).
# ---------------------------------------------------------------------------

def test_occupancy_shape_accepts_the_canonical_shape_unchanged():
    rows, notes = coerce_ticket_occupancy_prices_shape([
        {"occupancy": 1, "amount": 40}, {"occupancy": 2, "amount": 70},
    ])
    assert rows == [{"occupancy": 1, "amount": 40.0}, {"occupancy": 2, "amount": 70.0}]
    assert notes == []


def test_occupancy_shape_accepts_common_key_aliases():
    """A model correcting a price could plausibly write 'pax'/'price' instead of
    'occupancy'/'amount' - both are natural English words for the same thing."""
    rows, notes = coerce_ticket_occupancy_prices_shape([{"pax": 4, "price": 120}])
    assert rows == [{"occupancy": 4, "amount": 120.0}]
    assert notes == []


def test_occupancy_shape_drops_rows_above_the_bookable_cap():
    rows, notes = coerce_ticket_occupancy_prices_shape([
        {"occupancy": 9, "amount": 30}, {"occupancy": 12, "amount": 20},
    ], max_cap=9)
    assert rows == [{"occupancy": 9, "amount": 30.0}]
    assert len(notes) == 1


def test_occupancy_shape_drops_unreadable_rows_and_reports_them():
    rows, notes = coerce_ticket_occupancy_prices_shape([
        {"occupancy": 3, "amount": 55}, "not a row", {"occupancy": "n/a", "amount": 10}, {},
    ])
    assert rows == [{"occupancy": 3, "amount": 55.0}]
    assert len(notes) == 3


def test_occupancy_shape_rejects_a_non_list_entirely():
    rows, notes = coerce_ticket_occupancy_prices_shape({"occupancy": 2, "amount": 50})
    assert rows == []
    assert len(notes) == 1


def test_occupancy_shape_keeps_the_later_duplicate_and_notes_it():
    rows, notes = coerce_ticket_occupancy_prices_shape([
        {"occupancy": 2, "amount": 50}, {"occupancy": 2, "amount": 65},
    ])
    assert rows == [{"occupancy": 2, "amount": 65.0}]
    assert len(notes) == 1


# ---------------------------------------------------------------------------
# Child Price column (2026-08-13 product-owner request): "when child age is
# between 2 and 12, we must add a child price column next to adult price in
# pricing table" - default child_amount = adult amount, or the document's
# stated discount ratio when one exists.
# ---------------------------------------------------------------------------

def test_occupancy_shape_carries_child_amount_when_present():
    rows, notes = coerce_ticket_occupancy_prices_shape([
        {"occupancy": 1, "amount": 40, "child_amount": 20},
        {"occupancy": 2, "amount": 70, "child_amount": 35},
    ])
    assert rows == [
        {"occupancy": 1, "amount": 40.0, "child_amount": 20.0},
        {"occupancy": 2, "amount": 70.0, "child_amount": 35.0},
    ]
    assert notes == []


def test_occupancy_shape_accepts_child_amount_key_aliases():
    rows, notes = coerce_ticket_occupancy_prices_shape([{"pax": 3, "price": 90, "child_price": 45}])
    assert rows == [{"occupancy": 3, "amount": 90.0, "child_amount": 45.0}]
    assert notes == []


def test_occupancy_shape_omits_child_amount_key_when_never_given():
    """Rows with no child price at all shouldn't gain a fabricated child_amount key - the caller
    (render_ticket_pricing_editor) is what fills in the default-to-adult-price value."""
    rows, notes = coerce_ticket_occupancy_prices_shape([{"occupancy": 1, "amount": 40}])
    assert rows == [{"occupancy": 1, "amount": 40.0}]
    assert "child_amount" not in rows[0]


def test_occupancy_shape_drops_only_the_unreadable_child_price_keeping_the_adult_price():
    rows, notes = coerce_ticket_occupancy_prices_shape([{"occupancy": 1, "amount": 40, "child_amount": "n/a"}])
    assert rows == [{"occupancy": 1, "amount": 40.0}]
    assert len(notes) == 1


def test_child_price_ratio_defaults_to_one_when_no_distinct_child_rate_stated():
    assert resolve_ticket_child_price_ratio(40, 40) == 1.0


def test_child_price_ratio_defaults_to_one_when_nothing_extracted_at_all():
    assert resolve_ticket_child_price_ratio(0, 0) == 1.0


def test_child_price_ratio_reflects_a_stated_discount():
    # "child between 2 to 11.99 50% off" -> child price = adult price / 2.
    assert resolve_ticket_child_price_ratio(40, 20) == 0.5


def test_child_price_ratio_is_never_negative_or_a_divide_by_zero():
    assert resolve_ticket_child_price_ratio(0, 20) == 1.0
    assert resolve_ticket_child_price_ratio(-5, 20) == 1.0
    assert resolve_ticket_child_price_ratio(40, -5) == 1.0
