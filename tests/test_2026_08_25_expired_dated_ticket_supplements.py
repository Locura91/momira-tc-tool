"""Regression tests for a real product-owner rule (2026-08-25): "A Peak Season surcharge can
never have an End date earlier than today's date."

A Ticket Modality's dated supplement (a season, a holiday surcharge) whose own End Date has
already passed can never apply to any future booking - publishing it is dead weight at best.
build_ticket_payloads (builder.py) now reports these as expired_dated_supplements, checked
against the RESOLVED end date (after build_ticket_supplement_vos' own default-into-modality-
window/clip-into-modality-window logic), and render_publish_blockers (app.py) blocks publish
when any exist - same "expired data is a human problem, not a guessing game" precedent as the
pre-existing expired_validity_error for the whole ticket.

RETIRED (product owner, 2026-09-15): the old is_priced_choice ("Needs own Modality?") exclusion
is gone (see test_2026_09_15_needs_own_modality_retired.py) - every modality_supplements row now
reaches supplements_list and is subject to this same expiry check, whatever kind of extra it is.
"""
import builder
from test_builder_ticket import make_pre_config, minimal_ticket_data


def test_a_supplement_ended_before_today_is_reported_as_expired(fake_api_client):
    data = minimal_ticket_data(start_date="2020-01-01", end_date="2026-12-31", modality_supplements=[
        {"name": "Peak Season Surcharge", "adult_price_supplement": 22.5, "children_price_supplement": 22.5,
         "infant_price_supplement": 0, "start_date": "2024-12-24", "end_date": "2025-01-07"},
    ])
    result = builder.build_ticket_payloads(make_pre_config(), data, fake_api_client)
    assert len(result["expired_dated_supplements"]) == 1
    assert "Peak Season Surcharge" in result["expired_dated_supplements"][0]
    assert "2025-01-07" in result["expired_dated_supplements"][0]


def test_a_supplement_ending_in_the_future_is_not_reported(fake_api_client):
    data = minimal_ticket_data(start_date="2026-01-01", end_date="2027-12-31", modality_supplements=[
        {"name": "Peak Season Surcharge", "adult_price_supplement": 22.5, "children_price_supplement": 22.5,
         "infant_price_supplement": 0, "start_date": "2027-12-24", "end_date": "2028-01-07"},
    ])
    result = builder.build_ticket_payloads(make_pre_config(), data, fake_api_client)
    assert result["expired_dated_supplements"] == []


def test_an_undated_supplement_that_defaults_into_a_future_modality_window_is_not_flagged(fake_api_client):
    """A blank end_date defaults to the Modality's own end_date (build_ticket_supplement_vos) -
    if that's in the future, this is a normal always-on extra, not an expired one."""
    data = minimal_ticket_data(start_date="2026-01-01", end_date="2028-12-31", modality_supplements=[
        {"name": "German-speaking guide surcharge", "adult_price_supplement": 10,
         "children_price_supplement": 10, "infant_price_supplement": 0},
    ])
    result = builder.build_ticket_payloads(make_pre_config(), data, fake_api_client)
    assert result["expired_dated_supplements"] == []


def test_a_formerly_priced_choice_row_with_a_past_date_is_now_flagged_like_any_other(fake_api_client):
    """RETIRED (2026-09-15): is_priced_choice no longer excludes anything, so a row that would
    once have been a "Needs own Modality?" extra now publishes and is subject to the same expiry
    check as any other dated supplement."""
    data = minimal_ticket_data(start_date="2026-01-01", end_date="2027-12-31", modality_supplements=[
        {"name": "French-speaking guide", "adult_price_supplement": 15, "children_price_supplement": 15,
         "infant_price_supplement": 0, "start_date": "2020-01-01", "end_date": "2020-12-31"},
    ])
    result = builder.build_ticket_payloads(make_pre_config(), data, fake_api_client)
    assert len(result["expired_dated_supplements"]) == 1
    assert "French-speaking guide" in result["expired_dated_supplements"][0]


def test_multiple_expired_supplements_are_all_reported(fake_api_client):
    data = minimal_ticket_data(start_date="2020-01-01", end_date="2026-12-31", modality_supplements=[
        {"name": "Peak Season Surcharge", "adult_price_supplement": 22.5, "children_price_supplement": 22.5,
         "infant_price_supplement": 0, "start_date": "2024-12-24", "end_date": "2025-01-07"},
        {"name": "Easter Surcharge", "adult_price_supplement": 12, "children_price_supplement": 12,
         "infant_price_supplement": 0, "start_date": "2025-04-01", "end_date": "2025-04-10"},
    ])
    result = builder.build_ticket_payloads(make_pre_config(), data, fake_api_client)
    assert len(result["expired_dated_supplements"]) == 2


# ---------------------------------------------------------------------------
# Inverted date window (startDate > endDate) after clipping - a DIFFERENT bug than "expired"
# (audit, 2026-08-28). See build_ticket_supplement_vos' own docstring/comments for the full
# reasoning: a supplement whose own start falls AFTER the Modality's real, stable end can never
# be salvaged by clipping and must be dropped BEFORE clipping - but a genuinely past supplement
# on a Modality whose start got floored to today (the case covered by the tests above) must NOT
# be caught by the same check, since that's the expired-detection mechanism's job instead.
# ---------------------------------------------------------------------------

def test_a_supplement_starting_after_the_modality_ends_is_dropped_not_inverted():
    """CONFIRMED REAL BUG (audit, 2026-08-28): a mistyped year (or a date the source document
    only implies) can produce a supplement start that falls entirely after the Modality's own
    end. The old code only ever clipped `end` DOWN to the Modality's end while leaving `start`
    untouched, producing startDate > endDate - and when that clipped end still landed in the
    FUTURE, expired_dated_supplements' endDate < today check never caught it either, so the
    inverted range would have reached the live API silently. Reproduced directly against
    build_ticket_supplement_vos with a modality window that needs no today-flooring."""
    result = builder.build_ticket_supplement_vos(
        [{"name": "Peak Season Surcharge", "adult_price_supplement": 22.5,
          "children_price_supplement": 22.5, "infant_price_supplement": 0,
          "start_date": "2027-01-01", "end_date": "2027-01-10"}],
        modality_start="2026-01-01", modality_end="2026-08-31")
    assert result == []


def test_a_supplement_fully_inside_the_modality_window_is_unaffected_by_the_new_guard():
    result = builder.build_ticket_supplement_vos(
        [{"name": "Peak Season Surcharge", "adult_price_supplement": 22.5,
          "children_price_supplement": 22.5, "infant_price_supplement": 0,
          "start_date": "2026-03-01", "end_date": "2026-04-01"}],
        modality_start="2026-01-01", modality_end="2026-08-31")
    assert len(result) == 1
    assert result[0].startDate == "2026-03-01"
    assert result[0].endDate == "2026-04-01"


def test_a_supplement_starting_exactly_at_the_modality_end_is_kept():
    """Boundary case: starting ON the Modality's last day is still valid (a one-day window),
    only starting AFTER it is dropped."""
    result = builder.build_ticket_supplement_vos(
        [{"name": "Last Day Surcharge", "adult_price_supplement": 5,
          "children_price_supplement": 5, "infant_price_supplement": 0,
          "start_date": "2026-08-31", "end_date": "2026-09-15"}],
        modality_start="2026-01-01", modality_end="2026-08-31")
    assert len(result) == 1
    assert result[0].startDate == "2026-08-31"
    assert result[0].endDate == "2026-08-31"  # still clipped down to the modality's own end


def test_an_expired_supplement_on_a_floored_modality_start_is_still_kept_and_flagged(fake_api_client):
    """This is the case the guard must NOT touch - see the module docstring above. A supplement
    genuinely in the past, on a Modality whose OWN start_date is also in the past and therefore
    gets floored to today (start_date_or_today, in build_ticket_payloads), must still reach
    supplements_list and get caught by expired_dated_supplements - not be silently dropped by
    the new start-after-end guard, which only compares against the Modality's stable END."""
    data = minimal_ticket_data(start_date="2020-01-01", end_date="2026-12-31", modality_supplements=[
        {"name": "Old Peak Season Surcharge", "adult_price_supplement": 22.5,
         "children_price_supplement": 22.5, "infant_price_supplement": 0,
         "start_date": "2024-12-24", "end_date": "2025-01-07"},
    ])
    result = builder.build_ticket_payloads(make_pre_config(), data, fake_api_client)
    assert len(result["ticket_option_payload"]["supplements"]) == 1
    assert len(result["expired_dated_supplements"]) == 1
