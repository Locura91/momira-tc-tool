"""Regression tests for a real product-owner rule (2026-08-25): "A Peak Season surcharge can
never have an End date earlier than today's date."

A Ticket Modality's dated supplement (a season, a holiday surcharge - is_priced_choice=false)
whose own End Date has already passed can never apply to any future booking - publishing it is
dead weight at best. build_ticket_payloads (builder.py) now reports these as
expired_dated_supplements, checked against the RESOLVED end date (after build_ticket_supplement_
vos' own default-into-modality-window/clip-into-modality-window logic), and render_publish_
blockers (app.py) blocks publish when any exist - same "expired data is a human problem, not a
guessing game" precedent as the pre-existing expired_validity_error for the whole ticket.

A priced-choice row (is_priced_choice=true - see test_2026_08_25_ticket_priced_choice_extras.py)
never reaches supplements_list at all, so its dates are irrelevant here and it's never flagged.
"""
import builder
from test_builder_ticket import make_pre_config, minimal_ticket_data


def test_a_supplement_ended_before_today_is_reported_as_expired(fake_api_client):
    data = minimal_ticket_data(start_date="2020-01-01", end_date="2026-12-31", modality_supplements=[
        {"name": "Peak Season Surcharge", "adult_price_supplement": 22.5, "children_price_supplement": 22.5,
         "infant_price_supplement": 0, "start_date": "2024-12-24", "end_date": "2025-01-07",
         "is_priced_choice": False},
    ])
    result = builder.build_ticket_payloads(make_pre_config(), data, fake_api_client)
    assert len(result["expired_dated_supplements"]) == 1
    assert "Peak Season Surcharge" in result["expired_dated_supplements"][0]
    assert "2025-01-07" in result["expired_dated_supplements"][0]


def test_a_supplement_ending_in_the_future_is_not_reported(fake_api_client):
    data = minimal_ticket_data(start_date="2026-01-01", end_date="2027-12-31", modality_supplements=[
        {"name": "Peak Season Surcharge", "adult_price_supplement": 22.5, "children_price_supplement": 22.5,
         "infant_price_supplement": 0, "start_date": "2027-12-24", "end_date": "2028-01-07",
         "is_priced_choice": False},
    ])
    result = builder.build_ticket_payloads(make_pre_config(), data, fake_api_client)
    assert result["expired_dated_supplements"] == []


def test_an_undated_supplement_that_defaults_into_a_future_modality_window_is_not_flagged(fake_api_client):
    """A blank end_date defaults to the Modality's own end_date (build_ticket_supplement_vos) -
    if that's in the future, this is a normal always-on extra, not an expired one."""
    data = minimal_ticket_data(start_date="2026-01-01", end_date="2028-12-31", modality_supplements=[
        {"name": "German-speaking guide surcharge", "adult_price_supplement": 10,
         "children_price_supplement": 10, "infant_price_supplement": 0, "is_priced_choice": False},
    ])
    result = builder.build_ticket_payloads(make_pre_config(), data, fake_api_client)
    assert result["expired_dated_supplements"] == []


def test_a_priced_choice_row_with_a_past_date_is_not_flagged_as_expired(fake_api_client):
    """A "Needs own Modality?" row is excluded from supplements_list entirely before the expiry
    check runs - it never publishes on this Modality, so its own dates are irrelevant here."""
    data = minimal_ticket_data(start_date="2026-01-01", end_date="2027-12-31", modality_supplements=[
        {"name": "French-speaking guide", "adult_price_supplement": 15, "children_price_supplement": 15,
         "infant_price_supplement": 0, "start_date": "2020-01-01", "end_date": "2020-12-31",
         "is_priced_choice": True},
    ])
    result = builder.build_ticket_payloads(make_pre_config(), data, fake_api_client)
    assert result["expired_dated_supplements"] == []
    assert result["excluded_language_choice_extras"] == ["French-speaking guide"]


def test_multiple_expired_supplements_are_all_reported(fake_api_client):
    data = minimal_ticket_data(start_date="2020-01-01", end_date="2026-12-31", modality_supplements=[
        {"name": "Peak Season Surcharge", "adult_price_supplement": 22.5, "children_price_supplement": 22.5,
         "infant_price_supplement": 0, "start_date": "2024-12-24", "end_date": "2025-01-07",
         "is_priced_choice": False},
        {"name": "Easter Surcharge", "adult_price_supplement": 12, "children_price_supplement": 12,
         "infant_price_supplement": 0, "start_date": "2025-04-01", "end_date": "2025-04-10",
         "is_priced_choice": False},
    ])
    result = builder.build_ticket_payloads(make_pre_config(), data, fake_api_client)
    assert len(result["expired_dated_supplements"]) == 2
