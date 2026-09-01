"""Unit tests for builder.build_supplement_vos.

Covers the two confirmed product-owner rules baked into this function (see its docstring in
builder.py): modalityCodes must always be empty (a ClosedTour supplement applies to every
Modality, never just one) and refundable must always be False (ClosedTour supplements are
never refundable, regardless of the schema's own default) - plus the plain edge cases the
issue asked for (empty input, missing fields).
"""
from builder import build_supplement_vos


def test_empty_input_returns_empty_list():
    assert build_supplement_vos([]) == []
    assert build_supplement_vos(None) == []


def test_a_typical_supplement_round_trips():
    supplements = [{
        "name": "Balloon Ride", "price": 120, "mandatory": False, "on_request": True,
    }]
    vos = build_supplement_vos(supplements)
    assert len(vos) == 1
    vo = vos[0]
    assert vo.translations["EN"].name == "Balloon Ride"
    assert vo.price.singlePrice == 120.0
    assert vo.mandatory is False
    assert vo.onRequest is True


def test_modality_codes_are_always_empty_even_if_an_old_draft_sets_applies_to():
    """CONFIRMED PRODUCT-OWNER CORRECTION: supplements apply to every Modality, never one -
    see build_supplement_vos' docstring for the full story of why this was reversed."""
    supplements = [{"name": "Room upgrade", "price": 50, "applies_to": ["CABIN_A"]}]
    vo = build_supplement_vos(supplements)[0]
    assert vo.modalityCodes == []


def test_never_refundable_regardless_of_input():
    supplements = [{"name": "Excursion", "price": 30, "refundable": True}]
    vo = build_supplement_vos(supplements)[0]
    assert vo.refundable is False


def test_missing_price_fields_default_sensibly():
    """A supplement with nothing but a name must not raise - every price field should fall
    back to 0/the flat price, per _safe_supplement_price's fallback chain."""
    vos = build_supplement_vos([{"name": "Mystery add-on"}])
    vo = vos[0]
    assert vo.price.singlePrice == 0.0
    assert vo.price.doublePrice == 0.0
    assert vo.price.triplePrice == 0.0
    assert vo.price.quadruplePrice == 0.0
    assert vo.free is True  # price_val == 0


def test_single_and_double_fall_back_to_the_flat_price_when_not_given_separately():
    vos = build_supplement_vos([{"name": "Peak season surcharge", "price": 25}])
    vo = vos[0]
    assert vo.price.singlePrice == 25.0
    assert vo.price.doublePrice == 25.0
    # triple/quadruple are NOT inherited from the flat price - only 0 unless stated
    assert vo.price.triplePrice == 0.0
    assert vo.price.quadruplePrice == 0.0


def test_per_occupancy_prices_override_the_flat_price():
    vos = build_supplement_vos([{
        "name": "Per-room surcharge", "price": 71,
        "single_price": 71, "double_price": 35.5, "triple_price": 23.67, "quadruple_price": 17.75,
    }])
    vo = vos[0]
    assert vo.price.singlePrice == 71.0
    assert vo.price.doublePrice == 35.5
    assert vo.price.triplePrice == 23.67
    assert vo.price.quadruplePrice == 17.75


def test_travel_window_only_set_when_both_dates_present():
    with_both = build_supplement_vos([{
        "name": "Christmas surcharge", "price": 40,
        "travel_start_date": "2027-12-20", "travel_end_date": "2027-12-31",
    }])[0]
    assert with_both.travelWindows == [{"start": "2027-12-20", "end": "2027-12-31"}]

    with_one = build_supplement_vos([{
        "name": "Incomplete window", "price": 40, "travel_start_date": "2027-12-20",
    }])[0]
    assert with_one.travelWindows == []


def test_free_flag_tracks_whether_the_flat_price_is_zero():
    free = build_supplement_vos([{"name": "Free room upgrade", "price": 0}])[0]
    paid = build_supplement_vos([{"name": "Paid upgrade", "price": 10}])[0]
    assert free.free is True
    assert paid.free is False


def test_free_flag_is_false_when_only_per_occupancy_prices_carry_a_real_charge():
    """CONFIRMED BUG FIX (full-app audit HIGH, 2026-09-01, was builder.py:1137): a supplement
    priced ONLY via the per-occupancy columns (no flat "price" field at all) used to publish
    free=True, because only the flat price_val was checked and an absent "price" key defaults
    to 0 - even though singlePrice/doublePrice carry a real, non-zero charge Travel Compositor
    will actually read."""
    vo = build_supplement_vos([{
        "name": "Room upgrade", "single_price": 15, "double_price": 10,
    }])[0]
    assert vo.price.singlePrice == 15.0
    assert vo.price.doublePrice == 10.0
    assert vo.free is False


def test_free_flag_is_true_only_when_every_priced_field_is_genuinely_zero():
    vo = build_supplement_vos([{
        "name": "Genuinely free upgrade", "price": 0, "single_price": 0, "double_price": 0,
        "triple_price": 0, "quadruple_price": 0,
    }])[0]
    assert vo.free is True


def test_free_flag_is_false_when_only_triple_or_quadruple_price_carries_a_charge():
    vo = build_supplement_vos([{"name": "Family room surcharge", "triple_price": 12}])[0]
    assert vo.price.triplePrice == 12.0
    assert vo.free is False


def test_a_dict_accidentally_used_as_a_price_does_not_crash():
    """CONFIRMED FIX (real production crash, SUB-1): a supplement price arriving as the
    price_list's nested {"amount": ..., "currency": ...} shape instead of a flat number must
    not raise - _safe_supplement_price unwraps it."""
    vos = build_supplement_vos([{"name": "Confused shape", "price": {"amount": 45, "currency": "EUR"}}])
    assert vos[0].price.singlePrice == 45.0


def test_multiple_supplements_all_convert_independently():
    supplements = [{"name": "A", "price": 10}, {"name": "B", "price": 20, "mandatory": True}]
    vos = build_supplement_vos(supplements)
    assert len(vos) == 2
    assert vos[0].translations["EN"].name == "A"
    assert vos[1].translations["EN"].name == "B"
    assert vos[1].mandatory is True
