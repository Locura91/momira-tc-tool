"""Tests for the three-part stop-sale-agent request (product owner, 2026-09-27, verbatim):

  1. "Some suppliers send mails which we have to first match with a specific ticket,
     closedtour or hotel" -> Ticket is now a third matchable product type, alongside
     ClosedTour and Hotel, mirroring ClosedTour's fetch/apply shape exactly (both keep stop
     sales on their OPTIONS/modalities - schemas.py: ContractTicketModalityVO.stopSales is
     the same shape as ContractClosedTourOptionVO.stopSales).
  2. "one supplier mail has multiple products matched to the stop sales... one stopsale for
     closedtour can have multiple stop sales for multiple closedtours from the same
     supplier" -> stop_sales_parser splits one email into `additional_groups`; a human works
     through each one in turn via stop_sales_tool's group selector (checked here by source
     inspection, since the Streamlit UI itself can't run outside the app - established
     pattern, see test_2026_09_27_supplement_per_pax_toggle_and_rounding.py).

Sub-request 3 (an agent reading stop sales directly from a supplier's website, on an
automatic recurring check) is a separate, not-yet-built feature and has no tests here.
"""
import inspect

import ai_extractor
import stop_sales_parser as ssp
import stop_sales_tool as sst


# ======================================================================
# Parser: Ticket as a product type
# ======================================================================
def test_prompt_lists_ticket_as_a_product_type():
    assert '"Ticket"' in ssp.STOP_SALES_EXTRACTION_SYSTEM_PROMPT


# ======================================================================
# Parser: multiple products in one email -> additional_groups / all_groups
# ======================================================================
def _fake_call(data):
    def _call(system, user, model, max_tokens=4096):
        return data
    return _call


def test_single_product_email_has_no_additional_groups(monkeypatch):
    monkeypatch.setattr(ai_extractor, "_call_claude", _fake_call({
        "is_stop_sale": True, "product_identifier": "ASW-1", "product_type": "ClosedTour",
        "stop_sales": [{"start": "2026-08-12", "end": "2026-08-19"}],
        "confidence": "high",
    }))
    parsed = ssp.extract_stop_sales_from_email("body", subject="s", sent_date="2026-07-01")
    assert parsed["additional_groups"] == []
    assert len(ssp.all_groups(parsed)) == 1
    assert ssp.all_groups(parsed)[0]["product_identifier"] == "ASW-1"


def test_multi_product_email_returns_one_group_per_extra_product(monkeypatch):
    monkeypatch.setattr(ai_extractor, "_call_claude", _fake_call({
        "is_stop_sale": True, "product_identifier": "ASW-1", "product_type": "ClosedTour",
        "stop_sales": [{"start": "2026-08-12", "end": "2026-08-19"}],
        "confidence": "high",
        "additional_groups": [
            {"is_stop_sale": True, "product_identifier": "ASW-2", "product_type": "ClosedTour",
             "stop_sales": [{"start": "2026-09-01", "end": "2026-09-05"}], "confidence": "high"},
        ],
    }))
    parsed = ssp.extract_stop_sales_from_email("body", subject="s", sent_date="2026-07-01")
    assert len(parsed["additional_groups"]) == 1
    groups = ssp.all_groups(parsed)
    assert len(groups) == 2
    assert groups[0]["product_identifier"] == "ASW-1"
    assert groups[1]["product_identifier"] == "ASW-2"
    assert groups[1]["stop_sales"][0]["start"] == "2026-09-01"
    assert groups[1]["stop_sales"][0]["end"] == "2026-09-05"


def test_additional_group_with_no_valid_dates_is_dropped(monkeypatch):
    # A group not worth a human clicking through - see extract_stop_sales_from_email's
    # docstring: "only keep additional groups that actually found something."
    monkeypatch.setattr(ai_extractor, "_call_claude", _fake_call({
        "is_stop_sale": True, "product_identifier": "ASW-1", "product_type": "ClosedTour",
        "stop_sales": [{"start": "2026-08-12", "end": "2026-08-19"}],
        "confidence": "high",
        "additional_groups": [
            {"is_stop_sale": True, "product_identifier": "ASW-2", "product_type": "ClosedTour",
             "stop_sales": [], "confidence": "high"},
        ],
    }))
    parsed = ssp.extract_stop_sales_from_email("body", subject="s", sent_date="2026-07-01")
    assert parsed["additional_groups"] == []
    assert len(ssp.all_groups(parsed)) == 1


def test_all_groups_always_has_at_least_the_primary_even_with_missing_key(monkeypatch):
    monkeypatch.setattr(ai_extractor, "_call_claude", _fake_call({
        "is_stop_sale": True, "product_identifier": "ASW-1", "product_type": "ClosedTour",
        "stop_sales": [{"start": "2026-08-12", "end": "2026-08-19"}],
        "confidence": "high",
    }))
    parsed = ssp.extract_stop_sales_from_email("body", subject="s", sent_date="2026-07-01")
    parsed.pop("additional_groups", None)
    assert len(ssp.all_groups(parsed)) == 1


def test_coerce_group_normalizes_a_bare_dict_the_same_way_for_primary_and_extra():
    g1 = ssp._coerce_group({"product_identifier": "A", "is_release": True,
                            "stop_sales": [{"start": "2026-01-01", "end": "2026-01-02"}]})
    g2 = ssp._coerce_group({"product_identifier": "B", "is_release": False,
                            "stop_sales": [{"start": "2026-02-01", "end": "2026-02-02"}]})
    assert g1["is_release"] is True and g2["is_release"] is False
    assert g1["product_identifier"] == "A" and g2["product_identifier"] == "B"
    assert set(g1.keys()) == set(g2.keys())


# ======================================================================
# UI wiring (stop_sales_tool.py): Ticket support + group selector, via source inspection -
# Streamlit widgets can't run outside the real app (established pattern in this test suite).
# ======================================================================
def test_fetch_and_apply_ticket_functions_exist_and_mirror_closed_tour_shape():
    assert hasattr(sst, "fetch_ticket_options")
    assert hasattr(sst, "apply_to_ticket_option")


def test_fetch_ticket_options_uses_ticket_api_calls():
    src = inspect.getsource(sst.fetch_ticket_options)
    assert "client.get_ticket(" in src
    assert "client.get_ticket_option(" in src
    assert "modalityCodes" in src


def test_apply_to_ticket_option_uses_update_ticket_option():
    src = inspect.getsource(sst.apply_to_ticket_option)
    assert "client.update_ticket_option(" in src


def test_product_type_radio_includes_ticket():
    src = inspect.getsource(sst.render_stop_sales_tool)
    assert '_PTYPES = ["ClosedTour", "Hotel", "Ticket"]' in src


def test_targets_branch_treats_ticket_like_closed_tour():
    src = inspect.getsource(sst.render_stop_sales_tool)
    assert 'if product_type in ("ClosedTour", "Ticket"):' in src


def test_apply_step_dispatches_ticket_to_its_own_function():
    src = inspect.getsource(sst.render_stop_sales_tool)
    assert 'elif product_type == "Ticket":' in src
    assert "apply_to_ticket_option(client, supplier_id, product_code," in src


def test_group_selector_uses_all_groups_and_shows_when_more_than_one():
    src = inspect.getsource(sst.render_stop_sales_tool)
    assert "all_groups = ssp.all_groups(parsed)" in src
    assert "len(all_groups) > 1" in src


def test_group_suffix_keeps_processed_record_distinct_per_product():
    src = inspect.getsource(sst.render_stop_sales_tool)
    assert "mark_processed(fingerprint + group_suffix," in src


def test_widget_keys_that_vary_per_product_include_the_group_suffix():
    # Without this, switching between two products named in the same email would show one
    # product's modality/rate/room picks under the other product's widget.
    src = inspect.getsource(sst.render_stop_sales_tool)
    for key_expr in ('key=f"ss_ptype{group_suffix}"', 'key=f"ss_code{group_suffix}"',
                     'key=f"ss_modalities{group_suffix}"', 'key=f"ss_rates{group_suffix}"',
                     'key=f"ss_rooms{group_suffix}"', 'key=f"ss_apply{group_suffix}"'):
        assert key_expr in src, key_expr
