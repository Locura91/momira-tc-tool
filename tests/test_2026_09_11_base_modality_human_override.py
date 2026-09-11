"""Regression tests for the base-price-modality confirmation question (product owner, 2026-09-11,
asked directly after the scope-confirmation step shipped earlier the same day):

    "Should we also add one more question, if the selected modality we want to update in this
    exact update process, if this will be the base price or if it has to be calculated to the
    base price on top? Sedan Modality would be base price and if I would update the Hiace the
    price difference must be calculated and the price must then be added to the price
    supplement."

CONFIRMED REAL MOTIVATION: price_refresh._current_base_option auto-detects the base modality as
"whichever option currently carries no supplement" - exactly the live read Travel Compositor's own
API can get wrong (a real supplement reported as 0.0, already caught on real TRANSPORT-423015/
TRANSPORT-423134 the same day). A human who already knows the supplier's real structure (Sedan is
always base) needs to be able to say so directly, overriding that read for one round.

Mechanism under test: build_proposals doesn't touch base/supplement at all (see its own
docstring) - the override lives on the route dict itself as base_bracket_override, read by
rebuild_prices via _current_base_option(options, forced_bracket=...), so every caller that
receives the SAME route object (apply_proposals, preview_untouched_modality_effects) picks it up
automatically with no signature changes of their own.
"""
import price_refresh


def _route_two_modalities(sedan_price=175.0, hiace_price=200.0, sedan_supplement_live=0.0,
                          hiace_supplement_live=25.0, base_override=None):
    """Two-modality Transport route, options ordered Hiace-then-Sedan on purpose (order must
    never matter - only the bracket match does)."""
    route = {
        "id": "TRANSPORT-1", "name": "Test Route", "kind": None, "price_per_pax": False,
        "currency": "USD",
        "options": [
            {"code": "Hiace", "min_pax": 1, "max_pax": 8, "unit_price": hiace_price,
             "name": "Hiace",
             "raw": {"code": "Hiace",
                     "prices": [{"adultPriceSupplement": hiace_supplement_live,
                                "startDate": "2026-01-01", "endDate": "2049-12-31"}]
                     if hiace_supplement_live else []}},
            {"code": "Sedan", "min_pax": 1, "max_pax": 3, "unit_price": sedan_price,
             "name": "Sedan",
             "raw": {"code": "Sedan",
                     "prices": [{"adultPriceSupplement": sedan_supplement_live,
                                "startDate": "2026-01-01", "endDate": "2049-12-31"}]
                     if sedan_supplement_live else []}},
        ],
        "raw": {"id": "TRANSPORT-1", "pricePerPax": False, "vehiclePrice": sedan_price,
                "baseAdultPrice": 0.0, "baseChildrenPrice": 0.0, "baseInfantPrice": 0.0,
                "startDate": "2026-01-01", "endDate": "2049-12-31"},
    }
    if base_override is not None:
        route["base_bracket_override"] = base_override
    return route


# ----------------------------------------------------------------------
# _current_base_option - direct
# ----------------------------------------------------------------------

def test_forced_bracket_wins_over_the_live_zero_supplement_read():
    # Live data says Hiace has NO supplement (the exact corrupted-read shape) - without an
    # override this would wrongly pick Hiace as base. Forcing Sedan (1-3) must win instead.
    options = [
        {"code": "Hiace", "min_pax": 1, "max_pax": 8, "raw": {"prices": []}},
        {"code": "Sedan", "min_pax": 1, "max_pax": 3,
         "raw": {"prices": [{"adultPriceSupplement": -30.0}]}},
    ]
    chosen = price_refresh._current_base_option(options, forced_bracket=(1, 3))
    assert chosen["code"] == "Sedan"


def test_no_forced_bracket_keeps_the_original_auto_detect_behaviour():
    options = [
        {"code": "Hiace", "min_pax": 1, "max_pax": 8, "raw": {"prices": []}},
        {"code": "Sedan", "min_pax": 1, "max_pax": 3,
         "raw": {"prices": [{"adultPriceSupplement": -30.0}]}},
    ]
    chosen = price_refresh._current_base_option(options)
    assert chosen["code"] == "Hiace"  # unchanged: whichever reads zero-supplement live


def test_forced_bracket_not_present_on_this_route_falls_back_to_auto_detect():
    # The human's answer names a bracket this particular route doesn't have (e.g. a mixed
    # batch) - must never leave the route without any base at all.
    options = [
        {"code": "Hiace", "min_pax": 1, "max_pax": 8, "raw": {"prices": []}},
        {"code": "Van", "min_pax": 1, "max_pax": 15,
         "raw": {"prices": [{"adultPriceSupplement": 40.0}]}},
    ]
    chosen = price_refresh._current_base_option(options, forced_bracket=(1, 3))
    assert chosen["code"] == "Hiace"  # falls through to the zero-supplement heuristic


def test_forced_bracket_matching_two_options_falls_back_to_auto_detect():
    # Ambiguous forced match (two live options share the named bracket) - never guess which.
    options = [
        {"code": "SedanA", "min_pax": 1, "max_pax": 3, "raw": {"prices": []}},
        {"code": "SedanB", "min_pax": 1, "max_pax": 3,
         "raw": {"prices": [{"adultPriceSupplement": 10.0}]}},
    ]
    chosen = price_refresh._current_base_option(options, forced_bracket=(1, 3))
    assert chosen["code"] == "SedanA"  # zero-supplement heuristic, not a guess between the two


# ----------------------------------------------------------------------
# rebuild_prices - the override flows through from the route dict
# ----------------------------------------------------------------------

def test_rebuild_prices_with_no_override_reproduces_the_confirmed_real_423134_style_bug():
    # Both modalities read with no live supplement (the confirmed corrupted-read symptom) - with
    # no override, auto-detect keeps whichever HAPPENS to be zero-supplement (here: Sedan, since
    # it's listed second and ties go to the last zero-supplement candidate found... actually both
    # are zero-supplement, so len(zero_supplement) != 1 and it falls back to widest-bracket, i.e.
    # Hiace). This test pins today's behaviour so the "with override" test below shows the real
    # contrast, not an accidental one.
    route = _route_two_modalities(sedan_price=40.0, hiace_price=40.0,
                                  sedan_supplement_live=0.0, hiace_supplement_live=0.0)
    payloads = price_refresh.rebuild_prices(route, {"Sedan": 60.0})
    assert payloads["transport"]["vehiclePrice"] == 40.0  # Hiace (widest) was picked as base


def test_rebuild_prices_with_forced_base_bracket_uses_the_humans_answer_instead():
    route = _route_two_modalities(sedan_price=40.0, hiace_price=40.0,
                                  sedan_supplement_live=0.0, hiace_supplement_live=0.0,
                                  base_override=(1, 3))  # human says: Sedan is base
    payloads = price_refresh.rebuild_prices(route, {"Sedan": 60.0})
    assert payloads["transport"]["vehiclePrice"] == 60.0  # Sedan's NEW price becomes the base
    hiace_payload = next(o for o in payloads["options"] if o["code"] == "Hiace")
    # Hiace's own live price (40) didn't move, but the base moved to 60, so Hiace's supplement
    # must now be -20 to hold its own price steady at 40.
    hiace_supp = hiace_payload["payload"]["prices"][0]["adultPriceSupplement"]
    assert hiace_supp == -20.0


def test_updating_hiace_only_with_sedan_forced_as_base_computes_the_supplement_correctly():
    # The exact scenario in the product owner's own words: "Sedan Modality would be base price
    # and if I would update the Hiace the price difference must be calculated and the price must
    # then be added to the price supplement."
    route = _route_two_modalities(sedan_price=40.0, hiace_price=40.0,
                                  sedan_supplement_live=0.0, hiace_supplement_live=0.0,
                                  base_override=(1, 3))
    payloads = price_refresh.rebuild_prices(route, {"Hiace": 90.0})
    assert payloads["transport"]["vehiclePrice"] == 40.0  # Sedan (base) untouched this round
    hiace_payload = next(o for o in payloads["options"] if o["code"] == "Hiace")
    assert hiace_payload["payload"]["prices"][0]["adultPriceSupplement"] == 50.0  # 90 - 40


# ----------------------------------------------------------------------
# build_proposals wiring - route dict carries the override through to rebuild time
# ----------------------------------------------------------------------

def _finding_matching(price):
    return {"found": True, "minimum_pax": 1, "currency": "USD", "confidence": "high", "note": "",
            "matched_row": "row", "brackets": [{"min_pax": 1, "max_pax": 8, "price": price,
                                                 "child_price": None, "infant_price": None}]}


def test_a_scoped_hiace_only_round_with_forced_sedan_base_reprices_hiace_only():
    route = _route_two_modalities(sedan_price=40.0, hiace_price=40.0,
                                  sedan_supplement_live=0.0, hiace_supplement_live=0.0,
                                  base_override=(1, 3))
    finding = _finding_matching(90.0)
    proposal = price_refresh.build_proposals([route], {0: finding},
                                             scoped_brackets=[(1, 8)])[0]
    assert proposal["status"] == "changed"
    assert [c["code"] for c in proposal["changes"]] == ["Hiace"]
    # The override doesn't affect build_proposals' own diffing (only rebuild_prices at apply
    # time) - confirming it's still present on the route dict for the later apply step.
    assert route["base_bracket_override"] == (1, 3)
