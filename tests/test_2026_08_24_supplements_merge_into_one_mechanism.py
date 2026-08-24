"""Regression tests for the 2026-08-24 product-owner correction: "extra costs within tickets are
supplement by dates. No need to distinguish that at the app. All Extra costs are Supplement by dates
and can also be named all in one like this. Make sure, that supplements can be adjusted by dates
within the modality time."

This reverses the earlier 2026-08-12/13 split that sent a priced CHOICE (a foreign-language guide, a
Seat-in-Coach option) to its own separate Modality while only a genuinely dated change (a seasonal
table, a holiday surcharge) went through modality_supplements - Travel Compositor's own Ticket Modality
screen has exactly ONE mechanism for any of this ("Supplements by dates"), so the app no longer invents
a second one (render_ticket_extra_costs / extra_cost_options is gone from the live Ticket flow).

Also locks down the new rule that an undated supplement defaults to the Modality's OWN validity window
instead of being dropped, and that a supplement's dates get clipped to fall within that window - a
supplement can never be "live" when its own Modality isn't.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import builder
from test_builder_ticket import make_pre_config, minimal_ticket_data


class _FakeAPI:
    def __getattr__(self, name):
        return lambda *a, **k: {}


# ---------------------------------------------------------------------------
# build_ticket_supplement_vos: undated rows default to the modality window
# ---------------------------------------------------------------------------

def test_undated_supplement_defaults_to_the_modality_window():
    result = builder.build_ticket_supplement_vos(
        [{"name": "German-speaking guide", "adult_price_supplement": 10}],
        modality_start="2027-01-01", modality_end="2027-12-31",
    )
    assert len(result) == 1
    vo = result[0]
    assert vo.startDate == "2027-01-01"
    assert vo.endDate == "2027-12-31"
    assert vo.translations["EN"].name == "German-speaking guide"


def test_undated_supplement_with_no_modality_window_is_still_dropped():
    """Without a modality window to fall back on, an undated row still can't publish -
    TicketSupplementVO has no truly-optional-date shape."""
    result = builder.build_ticket_supplement_vos(
        [{"name": "German-speaking guide", "adult_price_supplement": 10}],
    )
    assert result == []


def test_supplement_dates_outside_the_modality_window_are_clipped_into_it():
    result = builder.build_ticket_supplement_vos(
        [{"name": "Early bird season", "adult_price_supplement": 5,
          "start_date": "2026-06-01", "end_date": "2028-06-01"}],
        modality_start="2027-01-01", modality_end="2027-12-31",
    )
    assert len(result) == 1
    vo = result[0]
    assert vo.startDate == "2027-01-01"
    assert vo.endDate == "2027-12-31"


def test_supplement_dates_fully_inside_the_modality_window_are_left_alone():
    result = builder.build_ticket_supplement_vos(
        [{"name": "Tet Holiday Surcharge", "adult_price_supplement": 47,
          "start_date": "2027-02-05", "end_date": "2027-02-09"}],
        modality_start="2027-01-01", modality_end="2027-12-31",
    )
    assert len(result) == 1
    vo = result[0]
    assert vo.startDate == "2027-02-05"
    assert vo.endDate == "2027-02-09"


# ---------------------------------------------------------------------------
# End-to-end: build_ticket_payloads wires the Modality's own resolved
# start/end dates into build_ticket_supplement_vos as the fallback/clip window.
# ---------------------------------------------------------------------------

def test_payload_build_defaults_an_undated_supplement_to_the_resolved_modality_dates(fake_api_client=None):
    data = minimal_ticket_data(modality_supplements=[
        {"name": "French-speaking guide", "adult_price_supplement": 8, "children_price_supplement": 8},
    ])
    data["start_date"] = "2027-03-01"
    data["end_date"] = "2027-09-30"
    result = builder.build_ticket_payloads(make_pre_config(), data, _FakeAPI())
    option = result["ticket_option_payload"]
    assert len(option["supplements"]) == 1
    supp = option["supplements"][0]
    assert supp["startDate"] == "2027-03-01"
    assert supp["endDate"] == "2027-09-30"
    assert supp["translations"]["EN"]["name"] == "French-speaking guide"


def test_payload_build_clips_a_supplement_that_overshoots_the_modality_window():
    data = minimal_ticket_data(modality_supplements=[
        {"name": "Peak season", "adult_price_supplement": 20,
         "start_date": "2020-01-01", "end_date": "2030-01-01"},
    ])
    data["start_date"] = "2027-03-01"
    data["end_date"] = "2027-09-30"
    result = builder.build_ticket_payloads(make_pre_config(), data, _FakeAPI())
    supp = result["ticket_option_payload"]["supplements"][0]
    assert supp["startDate"] == "2027-03-01"
    assert supp["endDate"] == "2027-09-30"
