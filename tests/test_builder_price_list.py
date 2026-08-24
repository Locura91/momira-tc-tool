"""Unit tests for builder.normalize_price_list.

See normalize_price_list's own docstring in builder.py: absent and zero are different
(an empty {} means "not sold", {"amount": 0} means "sold for free"), rows with no usable
price at all are dropped, and dates are normalised to ISO regardless of what format they
arrived in.
"""
import pytest

from builder import normalize_price_list


def test_full_row_keeps_every_occupancy():
    rows = [{
        "startDate": "2027-01-01", "endDate": "2027-03-31",
        "price": {"singlePrice": {"amount": 500}, "doublePrice": {"amount": 300},
                  "triplePrice": {"amount": 250}, "quadruplePrice": {"amount": 220}},
    }]
    out = normalize_price_list(rows, "EUR")
    assert len(out) == 1
    price = out[0]["price"]
    assert price["singlePrice"] == {"amount": 500.0, "currency": "EUR"}
    assert price["doublePrice"] == {"amount": 300.0, "currency": "EUR"}
    assert price["triplePrice"] == {"amount": 250.0, "currency": "EUR"}
    assert price["quadruplePrice"] == {"amount": 220.0, "currency": "EUR"}


def test_blank_occupancy_is_dropped_not_zeroed():
    """An empty {} for an unsold occupancy must not survive as a fake amount."""
    rows = [{
        "startDate": "2027-01-01", "endDate": "2027-03-31",
        "price": {"singlePrice": {"amount": 500}, "doublePrice": {}, "triplePrice": {}},
    }]
    out = normalize_price_list(rows, "EUR")
    price = out[0]["price"]
    assert "doublePrice" not in price
    assert "triplePrice" not in price
    assert price["singlePrice"]["amount"] == 500.0


def test_genuinely_free_occupancy_survives_as_zero():
    """{"amount": 0} means "sold, at no extra charge" - this must NOT be treated the same
    as an absent/blank occupancy, or a legitimately free upgrade disappears from the payload."""
    rows = [{"startDate": "2027-01-01", "endDate": "2027-03-31",
             "price": {"singlePrice": {"amount": 0}, "doublePrice": {"amount": 100}}}]
    out = normalize_price_list(rows, "EUR")
    assert out[0]["price"]["singlePrice"] == {"amount": 0.0, "currency": "EUR"}


def test_row_with_no_usable_price_is_dropped_entirely():
    rows = [
        {"startDate": "2027-01-01", "endDate": "2027-03-31", "price": {}},
        {"startDate": "2027-04-01", "endDate": "2027-06-30",
         "price": {"singlePrice": {"amount": 400}}},
    ]
    out = normalize_price_list(rows, "EUR")
    assert len(out) == 1
    assert out[0]["startDate"] == "2027-04-01"


def test_bare_number_price_is_wrapped():
    """A price entry as a bare number (rather than {"amount": ...}) must still normalise -
    this is the exact shape that used to crash _amt() in app.py (AttributeError on a bare
    float that had no .get())."""
    rows = [{"startDate": "2027-01-01", "endDate": "2027-03-31",
             "price": {"singlePrice": 500, "doublePrice": 300}}]
    out = normalize_price_list(rows, "EUR")
    assert out[0]["price"]["singlePrice"] == {"amount": 500.0, "currency": "EUR"}
    assert out[0]["price"]["doublePrice"] == {"amount": 300.0, "currency": "EUR"}


def test_display_dates_are_normalised_to_iso():
    """A row carrying DD/MM/YYYY (the house display format) must be converted to ISO before
    it ever reaches the API - Travel Compositor's LocalDate fields reject anything else."""
    rows = [{"startDate": "03/04/2027", "endDate": "30/09/2027",
             "price": {"singlePrice": {"amount": 500}}}]
    out = normalize_price_list(rows, "EUR")
    assert out[0]["startDate"] == "2027-04-03"
    assert out[0]["endDate"] == "2027-09-30"


def test_non_dict_rows_are_skipped_not_fatal():
    rows = [None, "not a row", 42, {"startDate": "2027-01-01", "endDate": "2027-03-31",
                                     "price": {"singlePrice": {"amount": 500}}}]
    out = normalize_price_list(rows, "EUR")
    assert len(out) == 1


def test_missing_currency_on_a_money_dict_falls_back_to_the_list_currency():
    rows = [{"startDate": "2027-01-01", "endDate": "2027-03-31",
             "price": {"singlePrice": {"amount": 100}}}]
    out = normalize_price_list(rows, "USD")
    assert out[0]["price"]["singlePrice"]["currency"] == "USD"


def test_child_discount_percentages_pass_through_when_present():
    rows = [{"startDate": "2027-01-01", "endDate": "2027-03-31",
             "price": {"singlePrice": {"amount": 500},
                       "tripleChildPercentageDiscount": 25,
                       "quadrupleChildPercentageDiscount": "30"}}]
    out = normalize_price_list(rows, "EUR")
    price = out[0]["price"]
    assert price["tripleChildPercentageDiscount"] == 25.0
    assert price["quadrupleChildPercentageDiscount"] == 30.0


def test_empty_input_returns_empty_list():
    assert normalize_price_list([], "EUR") == []
    assert normalize_price_list(None, "EUR") == []


def test_fallback_child_discount_applies_only_to_rows_selling_triple_or_quadruple():
    """CONFIRMED HOUSE RULE (product owner, 2026-08-24): a document-wide child discount
    percentage should reach every row's triple/quadruple discount even when the AI didn't repeat
    it on that specific row - but only where the row actually sells that occupancy (an
    unsold occupancy can't carry a discount, same as supplements)."""
    rows = [{"startDate": "2027-01-01", "endDate": "2027-03-31",
             "price": {"singlePrice": {"amount": 500}, "doublePrice": {"amount": 300},
                       "triplePrice": {"amount": 250}}}]
    out = normalize_price_list(rows, "EUR", fallback_child_discount_percentage=50)
    price = out[0]["price"]
    assert price["tripleChildPercentageDiscount"] == 50.0
    assert "quadrupleChildPercentageDiscount" not in price  # no quadruplePrice on this row


def test_fallback_child_discount_of_100_means_the_child_is_free():
    rows = [{"startDate": "2027-01-01", "endDate": "2027-03-31",
             "price": {"triplePrice": {"amount": 250}, "quadruplePrice": {"amount": 220}}}]
    out = normalize_price_list(rows, "EUR", fallback_child_discount_percentage=100)
    price = out[0]["price"]
    assert price["tripleChildPercentageDiscount"] == 100.0
    assert price["quadrupleChildPercentageDiscount"] == 100.0


def test_a_rows_own_explicit_discount_wins_over_the_fallback():
    rows = [{"startDate": "2027-01-01", "endDate": "2027-03-31",
             "price": {"triplePrice": {"amount": 250}, "tripleChildPercentageDiscount": 0}}]
    out = normalize_price_list(rows, "EUR", fallback_child_discount_percentage=50)
    # 0 is a real, confirmed "no discount" answer - the fallback must not override it.
    assert out[0]["price"]["tripleChildPercentageDiscount"] == 0.0


def test_no_fallback_given_leaves_missing_discounts_missing():
    rows = [{"startDate": "2027-01-01", "endDate": "2027-03-31",
             "price": {"triplePrice": {"amount": 250}}}]
    out = normalize_price_list(rows, "EUR", fallback_child_discount_percentage=None)
    assert "tripleChildPercentageDiscount" not in out[0]["price"]


@pytest.mark.parametrize("bad_amount", [None, "", "not-a-number", float("nan")])
def test_unusable_amount_is_treated_as_absent(bad_amount):
    rows = [{"startDate": "2027-01-01", "endDate": "2027-03-31",
             "price": {"singlePrice": {"amount": bad_amount}, "doublePrice": {"amount": 100}}}]
    out = normalize_price_list(rows, "EUR")
    assert "singlePrice" not in out[0]["price"]
    assert out[0]["price"]["doublePrice"]["amount"] == 100.0
