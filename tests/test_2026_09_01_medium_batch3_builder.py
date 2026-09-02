"""Tests for the MEDIUM/LOW "Batch 3" findings from the full-app audit
(full-app-audit-2026-09-01.md), fixed 2026-09-01 - covers builder.py, the last of the three
approved MEDIUM/LOW batches (Batch 1 = app.py, Batch 2 = extraction layer, Batch 3 = these
builder.py/schemas.py findings):

  1. coerce_price_list_shape had no alias entry for tripleChildPercentageDiscount/
     quadrupleChildPercentageDiscount, so a row's own value was silently dropped on every
     "Tell AI what to fix" merge or table re-render - fixed with a new
     _CHILD_DISCOUNT_COLUMN_ALIASES table, checked in both the nested-dict and
     flat-column branches of coerce_price_list_shape.
  2. The synthesized cancellation voucher's partial/no-refund branches said "within N days OF
     arrival" - the OPPOSITE condition from what the structured refund tier actually grants
     ("at least N days BEFORE arrival") - fixed to say "less than N days before arrival",
     matching the free-cancellation branch's already-correct wording.
  3. (Investigated, confirmed already fixed) the 30-day full-refund floor was found to already
     apply to both the structured field and the voucher prose, via the floored return value
     from _cancellation_ranges_from_tiers flowing into both - no code change needed here.
  4. Transport's per-vehicle multi-vehicle synthesis hardcoded child_price/infant_price to None
     unconditionally, unlike Transfer's sibling function which scales them by the same
     vehicles_needed multiplier when the source bracket priced them - fixed to match.
  5. endDate never went through the same to_iso_date "last line of defence" startDate gets via
     start_date_or_today - fixed with a new end_date_iso() helper, used at all 3 Ticket/
     Transfer/Transport call sites plus the Transfer supplement start/end dates.
  6. A non-string supplement date (e.g. datetime.date) crashed build_ticket_supplement_vos with
     an uncaught AttributeError ((value or "").strip() on a non-string) - fixed with the same
     str(...) coercion normalize_supplement_time already uses.
"""
import datetime

import builder


# ======================================================================
# 1. coerce_price_list_shape preserves child-discount columns
# ======================================================================
def test_coerce_preserves_triple_child_discount_from_nested_price_dict():
    rows, notes = builder.coerce_price_list_shape([
        {"startDate": "2027-01-01", "endDate": "2027-03-01",
         "price": {"triple": 100, "tripleChildPercentageDiscount": 50}}
    ])
    assert rows[0]["price"]["tripleChildPercentageDiscount"] == 50.0
    assert rows[0]["price"]["triplePrice"]["amount"] == 100.0


def test_coerce_preserves_quadruple_child_discount_from_flat_row():
    rows, notes = builder.coerce_price_list_shape([
        {"startDate": "2027-01-01", "endDate": "2027-03-01",
         "quadruple": 120, "quadrupleChildPercentageDiscount": 25}
    ])
    assert rows[0]["price"]["quadrupleChildPercentageDiscount"] == 25.0
    assert rows[0]["price"]["quadruplePrice"]["amount"] == 120.0


def test_coerce_drops_unparseable_child_discount_with_a_note():
    rows, notes = builder.coerce_price_list_shape([
        {"startDate": "2027-01-01", "endDate": "2027-03-01",
         "price": {"triple": 100, "tripleChildPercentageDiscount": "not-a-number"}}
    ])
    assert "tripleChildPercentageDiscount" not in rows[0]["price"]
    assert any("tripleChildPercentageDiscount" in n for n in notes)


def test_coerced_row_with_child_discount_survives_normalize_price_list():
    rows, _ = builder.coerce_price_list_shape([
        {"startDate": "2027-01-01", "endDate": "2027-03-01",
         "price": {"triple": 100, "tripleChildPercentageDiscount": 50}}
    ])
    normalized = builder.normalize_price_list(rows, "EUR")
    assert normalized[0]["price"]["tripleChildPercentageDiscount"] == 50.0


# ======================================================================
# 2. Voucher text uses "before arrival" wording for partial/no-refund tiers
# ======================================================================
def test_voucher_partial_refund_says_before_arrival_not_within_of():
    text = builder._cancellation_voucher_text(None, [(90, 100.0), (30, 50.0)])
    assert "50% cancellation fee if cancelled less than 30 days before arrival" in text
    assert "within 30 days of arrival" not in text


def test_voucher_no_refund_says_before_arrival_not_within_of():
    text = builder._cancellation_voucher_text(None, [(14, 0.0)])
    assert "No refund if cancelled less than 14 days before arrival." in text
    assert "within 14 days of arrival" not in text


def test_voucher_free_cancellation_branch_unchanged():
    text = builder._cancellation_voucher_text(None, [(30, 100.0)])
    assert "Free cancellation if cancelled at least 30 days before arrival." in text


def test_voucher_day_zero_branch_unchanged():
    text = builder._cancellation_voucher_text(None, [(0, 0.0)])
    assert "No refund for cancellations on the day of arrival or no-shows." in text


# ======================================================================
# 4. Transport multi-vehicle synthesis scales child/infant prices
# ======================================================================
def test_transport_synthesis_scales_child_and_infant_price_when_source_has_them():
    brackets = [{"min_occupancy": 1, "max_occupancy": 4, "price": 100.0,
                 "child_price": 60.0, "infant_price": 0.0}]
    extended = builder._extend_transport_brackets_for_multi_vehicle_pricing(brackets, price_per_pax=False)
    synthesized = [b for b in extended if b["min_occupancy"] == 5][0]
    assert synthesized["price"] == 200.0
    assert synthesized["child_price"] == 120.0
    assert synthesized["infant_price"] == 0.0


def test_transport_synthesis_leaves_child_price_none_when_source_never_priced_it():
    brackets = [{"min_occupancy": 1, "max_occupancy": 4, "price": 100.0,
                 "child_price": None, "infant_price": None}]
    extended = builder._extend_transport_brackets_for_multi_vehicle_pricing(brackets, price_per_pax=False)
    synthesized = [b for b in extended if b["min_occupancy"] == 5][0]
    assert synthesized["child_price"] is None
    assert synthesized["infant_price"] is None


def test_transport_synthesis_matches_transfer_siblings_scaling_behavior():
    tiers = [{"occupancy": 4, "price": 100.0, "child_price": 60.0, "infant_price": None}]
    extended = builder._extend_tiers_for_multi_vehicle_pricing(tiers, price_by_pax=False)
    transfer_synth = [t for t in extended if t["occupancy"] == 5][0]

    brackets = [{"min_occupancy": 1, "max_occupancy": 4, "price": 100.0,
                 "child_price": 60.0, "infant_price": None}]
    extended_t = builder._extend_transport_brackets_for_multi_vehicle_pricing(brackets, price_per_pax=False)
    transport_synth = [b for b in extended_t if b["min_occupancy"] == 5][0]

    assert transfer_synth["child_price"] == transport_synth["child_price"] == 120.0
    assert transfer_synth.get("infant_price") == transport_synth["infant_price"] is None


# ======================================================================
# 5. end_date_iso normalizes endDate the same way startDate is normalized
# ======================================================================
def test_end_date_iso_normalizes_dmy_format():
    assert builder.end_date_iso("31/12/2027") == "2027-12-31"


def test_end_date_iso_does_not_floor_a_past_date_to_today():
    # Unlike start_date_or_today, end_date_iso must NOT floor a past date - that's
    # expired_validity_window's job to catch and block on, not silently paper over.
    assert builder.end_date_iso("2020-01-01") == "2020-01-01"


def test_end_date_iso_empty_input_returns_empty_string():
    assert builder.end_date_iso(None) == ""
    assert builder.end_date_iso("") == ""


def test_ticket_payload_uses_end_date_iso_for_modality_end():
    import inspect
    source = inspect.getsource(builder.build_ticket_payloads)
    assert "_modality_end = end_date_iso(" in source


def test_transfer_payload_uses_end_date_iso():
    import inspect
    source = inspect.getsource(builder.build_transfer_payload)
    assert "effective_end_date = end_date_iso(" in source


def test_transport_payload_uses_end_date_iso():
    import inspect
    source = inspect.getsource(builder.build_transport_payloads)
    assert "effective_end_date = end_date_iso(" in source


# ======================================================================
# 6. build_ticket_supplement_vos survives a non-string date
# ======================================================================
def test_ticket_supplement_vos_survives_a_date_object_start_date():
    supplements = [{
        "name": "Peak Season Surcharge",
        "start_date": datetime.date(2027, 1, 1),
        "end_date": "2027-03-01",
        "adult_price_supplement": 10,
    }]
    result = builder.build_ticket_supplement_vos(supplements, modality_start="2027-01-01",
                                                  modality_end="2027-12-31")
    assert len(result) == 1
    assert result[0].startDate == "2027-01-01"


def test_ticket_supplement_vos_survives_a_date_object_end_date():
    supplements = [{
        "name": "Peak Season Surcharge",
        "start_date": "2027-01-01",
        "end_date": datetime.date(2027, 3, 1),
        "adult_price_supplement": 10,
    }]
    result = builder.build_ticket_supplement_vos(supplements, modality_start="2027-01-01",
                                                  modality_end="2027-12-31")
    assert len(result) == 1
    assert result[0].endDate == "2027-03-01"


def test_ticket_supplement_vos_still_works_with_plain_string_dates():
    supplements = [{
        "name": "Guide Language Surcharge",
        "start_date": "2027-02-01",
        "end_date": "2027-02-28",
        "adult_price_supplement": 5,
    }]
    result = builder.build_ticket_supplement_vos(supplements, modality_start="2027-01-01",
                                                  modality_end="2027-12-31")
    assert len(result) == 1
    assert result[0].startDate == "2027-02-01"
    assert result[0].endDate == "2027-02-28"
