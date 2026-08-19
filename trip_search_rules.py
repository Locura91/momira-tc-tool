"""
trip_search_rules.py — CONFIRMED PRODUCT-OWNER RULES (2026-08-19) for the AI Trip Idea
prototype: translates a customer's budget tier into concrete hotel/car search filters.

CONFIRMED RULE (product owner, 2026-08-19): "we must setup some rules for the AI Travel search:
Budget friendly means 3* hotel, small car (if requested). Superior means 4* hotel. Luxury means
5* hotel. Rule must be always with breakfast, hotel reviews minimum 8."

Deliberately implemented as CODE, not left for the model to reapply on every request - see
ai_extractor.py's whole pattern of routing pricing/business rules into builder.py rather than
trusting a prompt to reproduce them consistently every time. trip_prompt_extractor.py only
extracts WHICH tier the customer's words signal (budget/superior/luxury/unspecified); this
module is the single, deterministic place the tier turns into actual filters.

STILL OPEN (flagged, not guessed - see the "AI Trip Idea" project note):
  - The review score scale ("minimum 8") is assumed to be a /10 scale (the common OTA
    convention) - NOT yet confirmed against whatever scale Travel Compositor's own search API
    actually reports hotel reviews on. Confirm once that API is reachable; MIN_HOTEL_REVIEW_SCORE
    is the one constant to revisit if the real scale turns out to be different (e.g. /5).
  - Car size for Superior/Luxury tiers was not specified by the product owner - only Budget's
    "small car" was stated. Left as None (no size rule) for Superior/Luxury rather than guessed,
    so an unset filter falls back to whatever's naturally available rather than silently picking
    a size nobody asked for.
  - "small car" is stored as a human-readable tag, not yet mapped to Travel Compositor's actual
    vehicle-category enum - blocked on the same open booking-engine-access question as the rest
    of this prototype (see the project note).
"""
from typing import Any, Dict, Optional

BUDGET_TIERS = ("budget", "superior", "luxury", "unspecified")

# CONFIRMED RULE (product owner, 2026-08-19): "Budget friendly means 3* hotel... Superior means
# 4* hotel. Luxury means 5* hotel."
BUDGET_TIER_HOTEL_STARS: Dict[str, Optional[int]] = {
    "budget": 3,
    "superior": 4,
    "luxury": 5,
    "unspecified": None,       # no tier signal -> no star-rating filter applied
}

# CONFIRMED RULE (product owner, 2026-08-19): "small car (if requested)" - stated only for the
# Budget tier. "if requested" means this only ever applies when a car is actually part of the
# trip (see resolve_search_rules' car_wanted param) - a trip with no rental car in it gets no
# car-category filter regardless of tier.
BUDGET_TIER_CAR_CATEGORY: Dict[str, Optional[str]] = {
    "budget": "small",
    "superior": None,
    "luxury": None,
    "unspecified": None,
}

# CONFIRMED RULE (product owner, 2026-08-19): "Rule must be always with breakfast, hotel
# reviews minimum 8." Unconditional - every tier, not just paid-up ones.
ALWAYS_BOARD_TYPE = "breakfast"
ALWAYS_MIN_HOTEL_REVIEW_SCORE = 8  # assumed /10 scale - see module docstring


def resolve_search_rules(budget_tier: str, car_wanted: bool = False) -> Dict[str, Any]:
    """The one function this module needs: a budget tier (+ whether a car is wanted at all) in,
    the concrete filters that tier implies out. Always includes board_type and
    min_hotel_review_score, regardless of tier - see ALWAYS_* above."""
    tier = budget_tier if budget_tier in BUDGET_TIER_HOTEL_STARS else "unspecified"
    return {
        "budget_tier": tier,
        "hotel_star_rating": BUDGET_TIER_HOTEL_STARS[tier],
        "board_type": ALWAYS_BOARD_TYPE,
        "min_hotel_review_score": ALWAYS_MIN_HOTEL_REVIEW_SCORE,
        "car_category": BUDGET_TIER_CAR_CATEGORY[tier] if car_wanted else None,
    }
