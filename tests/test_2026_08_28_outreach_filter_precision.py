"""Regression tests for outreach search-quality fixes from the 2026-08-28 full-app audit
(product owner: "the outreach search shall have better outcomes").

Two of the filters that decide whether a found candidate becomes a usable supplier
(is_generic_name, is_ota_or_marketplace) used plain substring/prefix matching with no word
boundary - the exact class of bug EDITORIAL_PUBLISHER_PATTERNS already solved for "afar"
matching inside "safari". A real business whose name merely CONTAINED or STARTED WITH a
blocklisted word as a substring (not a whole word) was being silently rejected - "Kayaking
Excursions" (contains "kayak"), "Regatta" (contains "gta"), "Homeland Tours"
(starts with "home") - all plausible real Momira suppliers. Fixed with word-boundary regexes,
same pattern already used for the editorial-publisher check.

Also covers the LISTICLE_TITLE_PATTERN widening (titles like "Top Travel Agencies in Kenya"
that have neither a leading digit nor a year still weren't caught) and the reviews-query
max_results raise (was capped at 4 across three merged domains, starving the one query most
likely to surface a parseable star rating).

No test file previously existed for these functions - this is their first coverage.
"""
import outreach_discovery as od


# ---------------------------------------------------------------------------
# is_generic_name - word-boundary fix
# ---------------------------------------------------------------------------

def test_generic_name_still_rejects_the_boilerplate_it_always_caught():
    assert od.is_generic_name("Home") is True
    assert od.is_generic_name("Contact Us") is True
    assert od.is_generic_name("Log in") is True
    assert od.is_generic_name("about") is True


def test_generic_name_prefix_with_punctuation_still_rejected():
    # A generic word followed by a word boundary (space, punctuation) is still caught - this is
    # the legitimate case the old startswith() was trying to handle.
    assert od.is_generic_name("Home | Company Name") is True
    assert od.is_generic_name("Home - XYZ Travel") is True


def test_generic_name_no_longer_false_positives_on_a_real_business_name():
    """CONFIRMED REAL BUG (audit, 2026-08-28): "homeland".startswith("home") is True, so a real
    supplier legitimately named "Homeland Tours" was silently rejected as boilerplate."""
    assert od.is_generic_name("Homeland Tours") is False
    assert od.is_generic_name("Hometown Stays") is False


def test_generic_name_still_rejects_short_and_stopword_names():
    assert od.is_generic_name("In") is True
    assert od.is_generic_name("XY") is True
    assert od.is_generic_name(None) is True
    assert od.is_generic_name("") is True


# ---------------------------------------------------------------------------
# is_ota_or_marketplace - word-boundary fix
# ---------------------------------------------------------------------------

def test_ota_or_marketplace_still_catches_the_real_aggregators():
    assert od.is_ota_or_marketplace("Booking.com Malta") is True
    assert od.is_ota_or_marketplace("Kayak Travel Search") is True
    assert od.is_ota_or_marketplace("GetYourGuide Tours") is True


def test_ota_or_marketplace_no_longer_false_positives_on_containing_names():
    """CONFIRMED REAL BUG (audit, 2026-08-28): plain substring matching rejected any name
    merely CONTAINING a blocklisted word - "kayak" inside "Kayaking Excursions" (a plausible
    real supplier, Momira sources excursions), and "gta" (the DMC-platform abbreviation) inside
    ordinary words like "Regatta"."""
    assert od.is_ota_or_marketplace("Kayaking Excursions") is False
    assert od.is_ota_or_marketplace("Regatta Sailing Club") is False


def test_ota_or_marketplace_handles_empty_and_none():
    assert od.is_ota_or_marketplace(None) is False
    assert od.is_ota_or_marketplace("") is False


# ---------------------------------------------------------------------------
# LISTICLE_TITLE_PATTERN - widened to catch titles with no leading digit or year
# ---------------------------------------------------------------------------

def test_listicle_pattern_still_catches_the_originally_confirmed_cases():
    assert od.is_question_or_listicle_title("10 Best Nile Cruises") is True
    assert od.is_question_or_listicle_title("Best Nile River Cruises 2026/27") is True


def test_listicle_pattern_now_catches_category_titles_with_no_digit_or_year():
    """CONFIRMED REAL GAP (audit, 2026-08-28): "Top Travel Agencies in Kenya" and "Top 15 Tour
    Operators in Nairobi" have neither a leading digit nor a year, so both slipped through the
    old pattern unflagged."""
    assert od.is_question_or_listicle_title("Top Travel Agencies in Kenya") is True
    assert od.is_question_or_listicle_title("Top 15 Tour Operators in Nairobi") is True
    assert od.is_question_or_listicle_title("Best DMCs in Morocco") is True


def test_listicle_pattern_does_not_flag_a_real_company_name():
    assert od.is_question_or_listicle_title("Nile Cruise Egypt") is False
    assert od.is_question_or_listicle_title("Sahara Desert Safari Company") is False


# ---------------------------------------------------------------------------
# build_queries - reviews max_results raised
# ---------------------------------------------------------------------------

def test_reviews_query_max_results_raised_from_the_starved_default():
    queries = od.build_queries("Egypt", "Luxor", "Nile Cruise")
    reviews = next(q for q in queries if q["source"] == "reviews")
    assert reviews["max_results"] >= 10


# ---------------------------------------------------------------------------
# vet_candidates - review-count exception for a below-bar rating
# ---------------------------------------------------------------------------

def _candidate(rating=None, review_count=None, has_positive=False, snippet=""):
    return {"id": "x", "rating": rating, "reviewCount": review_count,
            "hasPositiveSignal": has_positive, "snippet": snippet, "name": "Some Supplier"}


def test_a_high_rating_low_review_count_candidate_still_passes_normally():
    kept = od.vet_candidates([_candidate(rating=5.0, review_count=1)])
    assert len(kept) == 1
    assert kept[0].get("keptOnReviewVolume") is not True


def test_below_bar_rating_with_strong_review_count_is_now_kept():
    """CONFIRMED PRODUCT-OWNER DECISION (2026-08-28, full-app audit): a well-established
    supplier at 3.8 stars across 500 reviews used to be auto-rejected outright, identically to
    one at 3.8 stars from a single review. A high review count is itself a strong real-business
    signal, so a candidate whose rating clears the (lower) review-count-exception floor AND
    whose review count clears the volume floor is let through."""
    kept = od.vet_candidates([_candidate(rating=3.8, review_count=500)])
    assert len(kept) == 1
    assert kept[0]["keptOnReviewVolume"] is True


def test_below_bar_rating_with_a_thin_review_count_is_still_rejected():
    kept = od.vet_candidates([_candidate(rating=3.8, review_count=5)])
    assert kept == []


def test_genuinely_poor_rating_is_never_rescued_by_review_volume_alone():
    """A high review count never rescues a rating below the review-count-exception's own lower
    floor - thousands of 2-star reviews is still a bad supplier."""
    kept = od.vet_candidates([_candidate(rating=2.5, review_count=10000)])
    assert kept == []


def test_build_selection_reason_names_the_review_volume_exception():
    reason = od.build_selection_reason({"rating": 3.8, "reviewCount": 500,
                                        "keptOnReviewVolume": True})
    assert "kept for its strong review count" in reason


def test_build_selection_reason_omits_the_note_for_a_normal_pass():
    reason = od.build_selection_reason({"rating": 4.8, "reviewCount": 20})
    assert "kept for its strong review count" not in reason
