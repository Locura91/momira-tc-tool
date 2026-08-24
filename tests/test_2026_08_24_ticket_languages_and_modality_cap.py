"""Regression tests for two more 2026-08-24 product-owner requests, both scoped to Tickets:

1. "we must include the language options within a ticket, as so far only one language is
   allowed. But often we receive two or more language options for the same price, if so, we
   must include it within the modality." - ContractTicketModalityVO.languages already existed
   in the schema and builder.py already passed it through; the gap was extraction and the UI
   never populating/surfacing it. These tests lock down the builder side (the UI multiselect
   and extraction prompts are covered by manual verification).

2. "please only allow one Modality creation within Ticket Creation - as the multiple ticket
   creation is not working yet." - render_ticket_extra_costs used to preview
   build_ticket_modality_combinations() as "N Modalities will be created", but Ticket creation
   never actually published more than the base Modality (a real gap between what the UI
   promised and what the app did). build_ticket_modality_combinations itself is unchanged and
   still generates every combination when asked - the app-level fix is capping the call site
   to max_modalities=1, tested here directly against the function it caps.
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
# Modality languages (same-price dual/multi-language options)
# ---------------------------------------------------------------------------

def test_multiple_same_price_languages_reach_the_published_modality():
    data = minimal_ticket_data(languages=["EN", "DE"])
    result = builder.build_ticket_payloads(make_pre_config(), data, _FakeAPI())
    assert result["ticket_option_payload"]["languages"] == ["EN", "DE"]


def test_no_languages_stated_defaults_to_english_only():
    result = builder.build_ticket_payloads(make_pre_config(), minimal_ticket_data(), _FakeAPI())
    assert result["ticket_option_payload"]["languages"] == ["EN"]


def test_an_empty_languages_list_still_falls_back_to_english():
    """`languages=[]` (e.g. extraction returned nothing) must not publish a Modality with NO
    language at all - Travel Compositor's own default is ["EN"]."""
    data = minimal_ticket_data(languages=[])
    result = builder.build_ticket_payloads(make_pre_config(), data, _FakeAPI())
    assert result["ticket_option_payload"]["languages"] == ["EN"]


# ---------------------------------------------------------------------------
# Ticket creation caps at one Modality - build_ticket_modality_combinations itself is
# unchanged (still generates every combination); the app now always calls it with
# max_modalities=1, which these tests exercise directly.
# ---------------------------------------------------------------------------

def test_uncapped_combinations_still_generate_every_variant():
    """Guards that the underlying combination generator itself wasn't touched - only its
    caller in app.py was capped. Two independent guide-language extras -> 3 combinations."""
    combos = builder.build_ticket_modality_combinations(
        {"adult": 40, "children": 40, "infant": 0},
        [{"name": "German guide", "group": "Guide language", "adult_price": 10, "children_price": 10, "infant_price": 0},
         {"name": "Lunch upgrade", "group": "", "adult_price": 5, "children_price": 5, "infant_price": 0}],
    )
    assert len(combos) == 4  # base, +German, +Lunch, +German+Lunch


def test_capped_at_one_only_the_base_modality_survives():
    combos = builder.build_ticket_modality_combinations(
        {"adult": 40, "children": 40, "infant": 0},
        [{"name": "German guide", "group": "Guide language", "adult_price": 10, "children_price": 10, "infant_price": 0},
         {"name": "Lunch upgrade", "group": "", "adult_price": 5, "children_price": 5, "infant_price": 0}],
        max_modalities=1,
    )
    assert len(combos) == 1
    assert combos[0]["is_base"] is True
    assert combos[0]["adult_price"] == 40


def test_a_ticket_with_extra_cost_rows_still_only_publishes_the_base_modality():
    """End-to-end: extra_cost_options describing a pricier variant must NOT change what
    build_ticket_payloads actually publishes - only the base Modality, at the base price."""
    data = minimal_ticket_data(extra_cost_options=[
        {"name": "German guide", "group": "Guide language", "adult_price": 10, "children_price": 10, "infant_price": 0},
    ])
    result = builder.build_ticket_payloads(make_pre_config(), data, _FakeAPI())
    assert result["ticket_option_payload"]["baseAdultPrice"] == data["base_adult_price"]
