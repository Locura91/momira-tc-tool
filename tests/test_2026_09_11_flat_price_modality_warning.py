"""Regression tests for the 2026-09-11 flat-price data-integrity warning in bulk Transport price
refresh, prompted directly by the product owner, reviewing real TRANSPORT-423134 (this app read
Sedan 1-3 pax as $60 while Hiace 1-8 pax read $40 — a genuine difference, which is what led to
noticing the surrounding routes in the same batch, several of which read BOTH modalities at the
SAME price):

    "if a transport has 2 or more modalities, there will be always base price and multiple price
    supplements. It does not make sense to have two modalities with the same price."

CONFIRMED REAL MECHANISM this exists to catch: Travel Compositor's public API can return
adultPriceSupplement: 0.0 for a supplement its own admin Prices tab shows as nonzero (already
caught once, same day, on real TRANSPORT-423015 - see apply_proposals' own comment and
claude/transport-supplement-admin-ui-vs-api-mismatch-2026-09-10.md). When that happens, EVERY
alternative modality on a transport reads back at exactly the shared base price, and the route
used to silently file as "unchanged" with nothing on screen to say why. This does not fix that
platform-side issue (there is no other field to read instead) - it makes the symptom visible
instead of silent.
"""
import price_refresh


def _route(sedan_price=40.0, hiace_price=40.0, sedan=(1, 3), hiace=(1, 8)):
    return {
        "id": "TRANSPORT-423134", "name": "Nuweiba - Sharm el Sheikh", "kind": None,
        "price_per_pax": False, "currency": "USD",
        "options": [
            {"code": "Sedan", "min_pax": sedan[0], "max_pax": sedan[1], "unit_price": sedan_price,
             "name": "Private Transfer with Car Sedan", "raw": {"code": "Sedan", "prices": []}},
            {"code": "Hiace", "min_pax": hiace[0], "max_pax": hiace[1], "unit_price": hiace_price,
             "name": "Private Transfer with Car Hiace", "raw": {"code": "Hiace", "prices": []}},
        ],
        "raw": {"id": "TRANSPORT-423134", "pricePerPax": False, "vehiclePrice": sedan_price,
                "baseAdultPrice": 0.0, "baseChildrenPrice": 0.0, "baseInfantPrice": 0.0},
    }


# ----------------------------------------------------------------------
# flat_price_modalities
# ----------------------------------------------------------------------

def test_two_alternative_modalities_at_the_same_price_are_flagged():
    # The real symptom: Sedan and Hiace both read $40 - the confirmed TC API-under-reporting bug.
    route = _route(sedan_price=40.0, hiace_price=40.0)
    assert set(price_refresh.flat_price_modalities(route)) == {"Sedan", "Hiace"}


def test_a_genuine_price_difference_is_not_flagged():
    # The real record's OWN reported case: Sedan $60, Hiace $40 - different, not suspect.
    route = _route(sedan_price=60.0, hiace_price=40.0)
    assert price_refresh.flat_price_modalities(route) == []


def test_a_single_modality_route_is_never_flagged():
    route = _route()
    route["options"] = [route["options"][0]]
    assert price_refresh.flat_price_modalities(route) == []


def test_tiered_not_alternative_brackets_are_never_flagged():
    # Disjoint brackets (party-size tiers of ONE product, e.g. 2-6 then 7-9) legitimately CAN
    # share a price - two tiers of the same vehicle costing the same isn't the rule this catches.
    route = _route(sedan=(2, 6), hiace=(7, 9), sedan_price=50.0, hiace_price=50.0)
    assert price_refresh.flat_price_modalities(route) == []


def test_an_unreadable_option_is_excluded_from_the_check():
    route = _route(sedan_price=40.0, hiace_price=40.0)
    route["options"][1]["fetch_failed"] = True
    assert price_refresh.flat_price_modalities(route) == []


def test_three_alternative_modalities_only_flags_the_ones_that_actually_tie():
    route = _route(sedan_price=40.0, hiace_price=40.0)
    route["options"].append({"code": "Van", "min_pax": 1, "max_pax": 15, "unit_price": 90.0,
                             "name": "Van", "raw": {"code": "Van", "prices": []}})
    assert set(price_refresh.flat_price_modalities(route)) == {"Sedan", "Hiace"}


def test_a_tiny_rounding_difference_still_counts_as_flat():
    route = _route(sedan_price=40.0, hiace_price=40.001)
    assert set(price_refresh.flat_price_modalities(route)) == {"Sedan", "Hiace"}


# ----------------------------------------------------------------------
# build_proposals wiring - flagged regardless of this round's status
# ----------------------------------------------------------------------

def _finding_matching(price):
    return {"found": True, "minimum_pax": 1, "currency": "USD", "confidence": "high", "note": "",
            "matched_row": "row", "brackets": [{"min_pax": 1, "max_pax": 3, "price": price,
                                                 "child_price": None, "infant_price": None}]}


def test_flagged_even_when_the_route_is_unchanged():
    route = _route(sedan_price=40.0, hiace_price=40.0)
    finding = _finding_matching(40.0)  # document also says 40 - nothing to propose
    proposal = price_refresh.build_proposals([route], {0: finding})[0]
    assert proposal["status"] == "unchanged"
    assert set(proposal["flat_price_codes"]) == {"Sedan", "Hiace"}


def test_flagged_even_when_the_route_is_not_in_the_document():
    route = _route(sedan_price=40.0, hiace_price=40.0)
    proposal = price_refresh.build_proposals([route], {})[0]
    assert proposal["status"] == "not_in_document"
    assert set(proposal["flat_price_codes"]) == {"Sedan", "Hiace"}


def test_flagged_even_when_a_change_is_being_proposed():
    route = _route(sedan_price=40.0, hiace_price=40.0)
    finding = _finding_matching(60.0)  # Sedan is about to change; Hiace is untouched this round
    proposal = price_refresh.build_proposals([route], {0: finding})[0]
    assert proposal["status"] == "changed"
    assert set(proposal["flat_price_codes"]) == {"Sedan", "Hiace"}


def test_not_flagged_when_prices_genuinely_differ():
    route = _route(sedan_price=60.0, hiace_price=40.0)
    finding = _finding_matching(60.0)
    proposal = price_refresh.build_proposals([route], {0: finding})[0]
    assert proposal["flat_price_codes"] == []
