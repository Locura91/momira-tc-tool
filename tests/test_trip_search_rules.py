"""Tests for trip_search_rules.py's budget-tier -> hotel/car filter mapping.

CONFIRMED PRODUCT-OWNER RULE (2026-08-19): "we must setup some rules for the AI Travel search:
Budget friendly means 3* hotel, small car (if requested). Superior means 4* hotel. Luxury means
5* hotel. Rule must be always with breakfast, hotel reviews minimum 8."
"""
import trip_search_rules as tsr


def test_budget_tier_is_3_star_with_small_car_when_a_car_is_wanted():
    rules = tsr.resolve_search_rules("budget", car_wanted=True)
    assert rules["hotel_star_rating"] == 3
    assert rules["car_category"] == "small"


def test_budget_tier_has_no_car_category_when_no_car_is_wanted():
    # "small car (if requested)" - the rule only applies when a car is actually part of the trip.
    rules = tsr.resolve_search_rules("budget", car_wanted=False)
    assert rules["hotel_star_rating"] == 3
    assert rules["car_category"] is None


def test_superior_tier_is_4_star():
    rules = tsr.resolve_search_rules("superior", car_wanted=True)
    assert rules["hotel_star_rating"] == 4


def test_luxury_tier_is_5_star():
    rules = tsr.resolve_search_rules("luxury", car_wanted=True)
    assert rules["hotel_star_rating"] == 5


def test_superior_and_luxury_have_no_car_category_rule_even_when_a_car_is_wanted():
    # Only Budget's car size was specified by the product owner - Superior/Luxury are left
    # unset rather than guessed.
    assert tsr.resolve_search_rules("superior", car_wanted=True)["car_category"] is None
    assert tsr.resolve_search_rules("luxury", car_wanted=True)["car_category"] is None


def test_unspecified_tier_has_no_star_rating_filter():
    rules = tsr.resolve_search_rules("unspecified", car_wanted=True)
    assert rules["hotel_star_rating"] is None
    assert rules["car_category"] is None


def test_breakfast_and_minimum_review_score_apply_to_every_tier_unconditionally():
    for tier in tsr.BUDGET_TIERS:
        rules = tsr.resolve_search_rules(tier, car_wanted=False)
        assert rules["board_type"] == "breakfast"
        assert rules["min_hotel_review_score"] == 8


def test_an_unrecognized_tier_string_falls_back_to_unspecified_rather_than_erroring():
    rules = tsr.resolve_search_rules("not-a-real-tier", car_wanted=True)
    assert rules["budget_tier"] == "unspecified"
    assert rules["hotel_star_rating"] is None
