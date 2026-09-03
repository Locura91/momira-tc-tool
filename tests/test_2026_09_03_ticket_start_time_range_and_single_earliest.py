"""Regression test for a real production API failure (product owner, 2026-09-03):

    "Couldn't create SEZ-T4's option: Cannot deserialize value of type `java.time.LocalTime`
    from String "07:50-08": Failed to deserialize `java.time.LocalTime`... Text '07:50-08' could
    not be parsed, unparsed text found at index 5 ... --> If mentioned a start time, just use the
    earliest time of all mentioned and just one, never like this: 07:50-08:30 / 07:45-08:30"

Root cause: a source describing a pickup WINDOW ("07:50-08:30") reached builder.py's
normalize_time_hhmm() as one whole string, and the old code just split on ":" without ever
noticing the dash - "07:50-08:30".split(":") -> ["07", "50-08", "30"] -> f"{parts[0]}:{parts[1]}"
-> "07:50-08", exactly the string in the real API error. Two fixes:

1. normalize_time_hhmm() now strips a range/window (splitting on "-", an en/em dash, or "to")
   before doing the HH:MM extraction, so it always returns a clean, valid "HH:MM" (or passes
   through untouched if genuinely unparseable).
2. build_ticket_payloads() now collapses the whole time_tables list down to AT MOST ONE entry -
   the single earliest - per the product owner's explicit rule that a Ticket only ever publishes
   ONE start time, however many lines/ranges/duplicates were extracted.
"""
from builder import normalize_time_hhmm, build_ticket_payloads
from schemas import TicketHumanPreConfig


def make_pre_config(**overrides):
    defaults = dict(
        supplier_id="48940", ticket_code="SEZ-T4", currency="EUR",
        modality_code="Standard", on_request=False,
    )
    defaults.update(overrides)
    return TicketHumanPreConfig(**defaults)


def minimal_ticket_data(**overrides):
    data = {
        "ticket_name": "La Digue: Bikes and Beaches", "description": "A test excursion.",
        "city": "Mahe", "manual_latitude": -4.3289, "manual_longitude": 55.7378,
        "base_adult_price": 50, "price_type": "DISTRIBUTION",
    }
    data.update(overrides)
    return data


# ======================================================================
# normalize_time_hhmm - the exact real-world failure and its shape variants
# ======================================================================
def test_the_exact_reported_range_is_reduced_to_its_earliest_clock_time():
    assert normalize_time_hhmm("07:50-08:30") == "07:50"
    assert normalize_time_hhmm("07:45-08:30") == "07:45"


def test_never_returns_the_broken_string_from_the_real_api_error():
    # This is the literal value the real API rejected - must never come out of the normalizer
    # again, whatever the input looks like.
    assert normalize_time_hhmm("07:50-08:30") != "07:50-08"


def test_en_dash_and_em_dash_windows_are_also_handled():
    assert normalize_time_hhmm("07:50–08:30") == "07:50"  # en dash
    assert normalize_time_hhmm("07:50—08:30") == "07:50"  # em dash


def test_word_to_window_is_also_handled():
    assert normalize_time_hhmm("7:50 to 8:30") == "07:50"


def test_single_digit_hour_is_zero_padded():
    assert normalize_time_hhmm("9:00") == "09:00"


def test_ordinary_hhmm_and_hhmmss_still_work():
    assert normalize_time_hhmm("08:00") == "08:00"
    assert normalize_time_hhmm("08:00:00") == "08:00"


def test_blank_input_stays_blank():
    assert normalize_time_hhmm("") == ""
    assert normalize_time_hhmm(None) == ""


# ======================================================================
# build_ticket_payloads - end-to-end: never sends a broken timeTables value, and never
# more than the single earliest entry
# ======================================================================
def test_a_range_reaching_the_builder_whole_is_published_as_one_clean_earliest_time(fake_api_client):
    data = minimal_ticket_data(time_tables=["07:50-08:30", "07:45-08:30"])
    result = build_ticket_payloads(make_pre_config(), data, fake_api_client)
    assert result["main_ticket_error"] is None
    assert result["ticket_option_error"] is None
    assert result["ticket_option_payload"]["timeTables"] == ["07:45"]


def test_several_genuinely_distinct_times_still_collapse_to_just_the_earliest(fake_api_client):
    """Product-owner rule: "just use the earliest time of all mentioned and just one" - applies
    even when the times aren't a malformed range, e.g. two clean HH:MM entries."""
    data = minimal_ticket_data(time_tables=["14:00", "09:00"])
    result = build_ticket_payloads(make_pre_config(), data, fake_api_client)
    assert result["ticket_option_payload"]["timeTables"] == ["09:00"]


def test_no_time_stated_publishes_an_empty_list(fake_api_client):
    data = minimal_ticket_data(time_tables=[])
    result = build_ticket_payloads(make_pre_config(), data, fake_api_client)
    assert result["ticket_option_payload"]["timeTables"] == []
