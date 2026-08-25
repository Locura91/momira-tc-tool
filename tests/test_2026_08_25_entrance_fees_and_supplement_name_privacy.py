"""Regression tests for two 2026-08-25 product-owner requests, both scoped to Tickets:

1. "if the Ticket description from the supplier says, no Entrance fees included, this
   information must be stated in the Title within (), and it shall be displayed as a bullet
   point at the Voucher Remark, as this information is very important." Confirms
   entrance_fees_excluded appends "(Entrance fees not included)" to both the main ticket name
   and the datasheet name, and prepends a bullet to the composed voucher text (which the
   Modality's own remarks share verbatim - see _with_manual_notes/_with_what_to_bring history).

2. "within the supplement name, please do not write any% to it and never a price, because the
   client can see that information and he should not see it." Confirms sanitize_supplement_name
   strips percentage/currency figures out of a Ticket Modality supplement's customer-facing name,
   both directly and through the full build_ticket_payloads pipeline.
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
# Entrance fees notice
# ---------------------------------------------------------------------------

def test_entrance_fees_excluded_appends_notice_to_both_title_fields():
    data = minimal_ticket_data()
    data["ticket_name"] = "Luxor Highlights Tour"
    data["entrance_fees_excluded"] = True
    result = builder.build_ticket_payloads(make_pre_config(), data, _FakeAPI())
    main = result["main_ticket_payload"]
    assert main["name"] == "Luxor Highlights Tour (Entrance fees not included)"
    assert main["datasheets"]["EN"]["name"] == "Luxor Highlights Tour (Entrance fees not included)"


def test_entrance_fees_excluded_does_not_mutate_the_source_ticket_name():
    """Rebuilding the same data dict twice (e.g. a 'rebuild payload' click) must never double
    up the suffix - the source field itself must stay untouched."""
    data = minimal_ticket_data()
    data["ticket_name"] = "Luxor Highlights Tour"
    data["entrance_fees_excluded"] = True
    builder.build_ticket_payloads(make_pre_config(), data, _FakeAPI())
    assert data["ticket_name"] == "Luxor Highlights Tour"
    result2 = builder.build_ticket_payloads(make_pre_config(), data, _FakeAPI())
    assert result2["main_ticket_payload"]["name"] == "Luxor Highlights Tour (Entrance fees not included)"


def test_no_entrance_fees_notice_when_not_flagged():
    data = minimal_ticket_data()
    data["ticket_name"] = "Luxor Highlights Tour"
    result = builder.build_ticket_payloads(make_pre_config(), data, _FakeAPI())
    assert result["main_ticket_payload"]["name"] == "Luxor Highlights Tour"


def test_entrance_fees_bullet_is_first_line_of_the_composed_voucher_text():
    data = minimal_ticket_data()
    data["entrance_fees_excluded"] = True
    data["voucher_remarks"] = "Free cancellation up to 30 days before arrival."
    result = builder.build_ticket_payloads(make_pre_config(), data, _FakeAPI())
    voucher = result["main_ticket_payload"]["datasheets"]["EN"]["voucherRemarks"]
    assert voucher.startswith("• Entrance fees are NOT included in this price.")
    assert "Free cancellation up to 30 days before arrival." in voucher


def test_entrance_fees_bullet_also_reaches_the_modality_remarks():
    """Voucher Remarks and the Modality's own remarks must never diverge - see the 2026-08-24
    audit fix this builds on (build_ticket_payloads composes ONE text used for both)."""
    data = minimal_ticket_data()
    data["entrance_fees_excluded"] = True
    result = builder.build_ticket_payloads(make_pre_config(), data, _FakeAPI())
    main_voucher = result["main_ticket_payload"]["datasheets"]["EN"]["voucherRemarks"]
    modality_remarks = result["ticket_option_payload"]["remarks"]["EN"]["remarks"]
    assert main_voucher == modality_remarks
    assert modality_remarks.startswith("• Entrance fees are NOT included in this price.")


# ---------------------------------------------------------------------------
# Supplement name must never carry a percentage or price
# ---------------------------------------------------------------------------

def test_sanitize_supplement_name_strips_percentages():
    assert builder.sanitize_supplement_name("Tet Holiday Surcharge (+15%)") == "Tet Holiday Surcharge"
    assert builder.sanitize_supplement_name("High Season 15%") == "High Season"


def test_sanitize_supplement_name_strips_currency_amounts():
    assert builder.sanitize_supplement_name("Easter surcharge $15") == "Easter surcharge"
    assert builder.sanitize_supplement_name("High Season - 15 EUR") == "High Season"
    assert builder.sanitize_supplement_name("Christmas surcharge 15.50€") == "Christmas surcharge"


def test_sanitize_supplement_name_leaves_clean_names_alone():
    assert builder.sanitize_supplement_name("German-speaking guide") == "German-speaking guide"
    assert builder.sanitize_supplement_name("Tet Holiday Surcharge") == "Tet Holiday Surcharge"


def test_sanitize_supplement_name_falls_back_when_nothing_survives():
    assert builder.sanitize_supplement_name("15%") == "Seasonal surcharge"
    assert builder.sanitize_supplement_name("") == ""


def test_build_ticket_supplement_vos_publishes_the_sanitized_name():
    result = builder.build_ticket_supplement_vos(
        [{"name": "Tet Holiday Surcharge (+15%)", "adult_price_supplement": 15,
          "start_date": "2027-02-05", "end_date": "2027-02-09"}],
    )
    assert result[0].translations["EN"].name == "Tet Holiday Surcharge"


def test_full_payload_build_never_leaks_a_price_in_the_supplement_name():
    data = minimal_ticket_data(modality_supplements=[
        {"name": "Tet Holiday Surcharge (+15%)", "adult_price_supplement": 15,
         "start_date": "2027-02-05", "end_date": "2027-02-09"},
    ])
    data["start_date"] = "2027-01-01"
    data["end_date"] = "2027-12-31"
    result = builder.build_ticket_payloads(make_pre_config(), data, _FakeAPI())
    name = result["ticket_option_payload"]["supplements"][0]["translations"]["EN"]["name"]
    assert name == "Tet Holiday Surcharge"
    assert "%" not in name and "$" not in name and "EUR" not in name
