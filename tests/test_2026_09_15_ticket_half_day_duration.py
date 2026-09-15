"""Regression test for a real production bug (product owner, 2026-09-15): "Ticket creation:
Half day cannot be 0,5 days, travel c translates it to 4 days - if half day, just leave
estimated duration empty."

Root cause: build_ticket_payloads forwarded whatever numeric duration + unit the human entered
(or the AI extracted) straight to Travel Compositor unmodified. A half-day duration is naturally
entered as 0.5 with unit "DAYS" (the only UI/extraction shape for "half a day" - there is no
"HALF_DAYS" unit anywhere in this app or in Travel Compositor's own API), and Travel Compositor's
own backend does not accept a fractional day count - it silently reinterprets it as 4 whole days
instead of erroring, which is what the product owner saw live.

Fix: builder._resolve_ticket_duration() detects a fractional ("DAYS", non-whole) duration and
sends nothing instead (the schema's own duration=0.0/durationType="HOURS" defaults - the same
"not set" shape the app already uses for a genuinely blank duration, see
test_2026_09_03_ticket_duration_unit_display.py) rather than letting the fraction reach the API.
A fractional HOURS duration (e.g. 1.5 hours, a real and unambiguous value TC accepts) is left
untouched - only DAYS is affected.

Applied at BOTH places build_ticket_payloads builds a duration - the main ticket
(ApiStaticContentTicketVO) and its first Modality/option (ContractTicketModalityVO) - since a
half-day extraction/entry ends up in both kwargs dicts identically.
"""
from builder import _resolve_ticket_duration, build_ticket_payloads
from schemas import TicketHumanPreConfig


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


# ======================================================================
# _resolve_ticket_duration - the unit helper itself
# ======================================================================
def test_half_day_is_cleared_to_empty_not_sent_as_a_fraction():
    duration, duration_type = _resolve_ticket_duration(0.5, "DAYS")
    assert duration == 0.0
    assert duration_type == "HOURS"


def test_a_whole_number_of_days_is_left_untouched():
    duration, duration_type = _resolve_ticket_duration(2, "DAYS")
    assert duration == 2.0
    assert duration_type == "DAYS"


def test_a_fractional_hours_duration_is_left_untouched_only_days_is_special_cased():
    duration, duration_type = _resolve_ticket_duration(1.5, "HOURS")
    assert duration == 1.5
    assert duration_type == "HOURS"


def test_a_blank_duration_stays_blank():
    duration, duration_type = _resolve_ticket_duration(0, "HOURS")
    assert duration == 0.0
    assert duration_type == "HOURS"


def test_a_missing_duration_type_defaults_to_hours_and_is_not_treated_as_days():
    duration, duration_type = _resolve_ticket_duration(0.5, None)
    assert duration == 0.5
    assert duration_type == "HOURS"


# ======================================================================
# build_ticket_payloads wiring - both the main ticket and the option
# ======================================================================
def test_half_day_never_reaches_the_main_ticket_payload(fake_api_client):
    data = minimal_ticket_data(duration=0.5, duration_type="DAYS")
    result = build_ticket_payloads(make_pre_config(), data, fake_api_client)
    assert result["main_ticket_payload"]["duration"] == 0.0
    assert result["main_ticket_payload"]["durationType"] == "HOURS"


def test_half_day_never_reaches_the_ticket_option_payload(fake_api_client):
    data = minimal_ticket_data(duration=0.5, duration_type="DAYS")
    result = build_ticket_payloads(make_pre_config(), data, fake_api_client)
    assert result["ticket_option_payload"]["duration"] == 0.0
    assert result["ticket_option_payload"]["durationType"] == "HOURS"


def test_a_real_whole_day_duration_still_publishes_correctly(fake_api_client):
    data = minimal_ticket_data(duration=2, duration_type="DAYS")
    result = build_ticket_payloads(make_pre_config(), data, fake_api_client)
    assert result["main_ticket_payload"]["duration"] == 2.0
    assert result["main_ticket_payload"]["durationType"] == "DAYS"
    assert result["ticket_option_payload"]["duration"] == 2.0
    assert result["ticket_option_payload"]["durationType"] == "DAYS"
