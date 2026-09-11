"""Regression tests for the 2026-09-11 work on bulk Transport price refresh and MULTIPLE
MODALITIES, prompted by the product owner reviewing the flow against real TRANSPORT-418748:

    "One Modality for Sedan, 1 to 3 Pax. A second modality for Hiace, 1 to 8 Pax. PriceVehicle =
    price Sedan. Price Hiace = Price Hiace from file - price from Sedan and the difference of
    this price is included as price supplement. [...] The bulk price update was already working
    fine, when there is only one price. But now we have to work with more modalities."

and, on whether a bracket mismatch should block an explicitly scoped round:

    "the App must understand the difference between BasePrice (per vehicle or per Pax)"

Three separate confirmed bugs and one confirmed feature, all locked down here.

BUG 1 - a document bracket bleeding across modalities that are ALTERNATIVES. Reproduced against
the real record's shape (Sedan 1-3 at 175, Hiace 1-8 at 200): a rate sheet pricing only the 1-3
Sedan line produced BOTH "Sedan 175 -> 95" and "Hiace 200 -> 95", wiping the whole Hiace
supplement off a modality the document never mentioned. Cause: Sedan 1-3 and Hiace 1-8 both start
at 1 pax, so they overlap, and bracket_price_for's overlap fallback happily handed the Sedan price
to Hiace. The FTS path was already guarded (only_option_code) but NOTHING else was - and the AI
path is what every non-FTS supplier uses.

BUG 2 - a PER-VEHICLE price multiplied by party size. bracket_price_for's minimum-party rule
("$32 p.p., minimum 2 pax" means a lone traveller pays 64) is a per-PERSON rule. Applied to a
per-vehicle transport it turned a real $45 vehicle rate into $90. A vehicle costs what it costs
regardless of how many people ride in it.

BUG 3 - see test_2026_09_11_fts_matrix_city_resolution.py for the matching rewrite; not repeated
here.

FEATURE - the human modality confirmation step the product owner asked for: "the app detects all
modalities first, and before the AI reads the document, the app asks the human which modality it
is and which price it shall touch." Confirmed shape: optional, everything preselected.
"""
import price_refresh


def _route(sedan=(1, 3), hiace=(1, 8), per_pax=False, sedan_price=175.0, hiace_price=200.0):
    """The real TRANSPORT-418748 shape: per-vehicle, Sedan 1-3 as base, Hiace 1-8 + supplement."""
    return {
        "id": "TRANSPORT-418748", "name": "Marsa Matruh - Siwa Oasis", "kind": None,
        "price_per_pax": per_pax, "currency": "USD",
        "options": [
            {"code": "Sedan", "min_pax": sedan[0], "max_pax": sedan[1], "unit_price": sedan_price,
             "name": "Private Transfer with Car Sedan - 1 to 3 Pax",
             "raw": {"code": "Sedan", "prices": []}},
            {"code": "Hiace", "min_pax": hiace[0], "max_pax": hiace[1], "unit_price": hiace_price,
             "name": "Private Transfer with Car Hiace - 1 to 8 Pax",
             "raw": {"code": "Hiace",
                     "prices": [{"adultPriceSupplement": hiace_price - sedan_price}]}},
        ],
        "raw": {"id": "TRANSPORT-418748", "pricePerPax": per_pax,
                "vehiclePrice": 0.0 if per_pax else sedan_price,
                "baseAdultPrice": sedan_price if per_pax else 0.0,
                "baseChildrenPrice": 0.0, "baseInfantPrice": 0.0},
    }


def _finding(brackets, minimum_pax=1):
    return {"found": True, "minimum_pax": minimum_pax, "currency": "USD", "confidence": "high",
            "note": "", "matched_row": "row", "brackets": [
                {"min_pax": lo, "max_pax": hi, "price": price,
                 "child_price": None, "infant_price": None} for lo, hi, price in brackets]}


def _changes(route, finding, **kw):
    proposals = price_refresh.build_proposals([route], {0: finding}, **kw)
    return {c["code"]: c["new"] for c in proposals[0]["changes"]}


# ----------------------------------------------------------------------
# BUG 1 - alternatives vs tiers
# ----------------------------------------------------------------------

def test_a_sheet_pricing_only_the_sedan_line_never_moves_the_hiace_price():
    # THE reported bug, in one assertion. Before: {"Sedan": 95.0, "Hiace": 95.0}.
    assert _changes(_route(), _finding([(1, 3, 95.0)])) == {"Sedan": 95.0}


def test_a_sheet_pricing_only_the_hiace_line_never_moves_the_sedan_price():
    assert _changes(_route(), _finding([(1, 8, 120.0)])) == {"Hiace": 120.0}


def test_both_lines_priced_still_reprices_both():
    assert _changes(_route(), _finding([(1, 3, 95.0), (1, 8, 120.0)])) == \
        {"Sedan": 95.0, "Hiace": 120.0}


def test_tiered_brackets_keep_the_overlap_fallback_they_have_always_relied_on():
    # Disjoint live brackets are party-size TIERS of one product, not alternative vehicles - a
    # document pricing "2-9" legitimately tells you what a live "2-6" and a live "7-9" both cost.
    # This is the behaviour every non-FTS supplier's rate sheet already depends on and it must
    # NOT be tightened by the alternatives fix.
    route = _route(sedan=(2, 6), hiace=(7, 9))
    assert _changes(route, _finding([(2, 9, 80.0)])) == {"Sedan": 80.0, "Hiace": 80.0}


def test_options_are_alternatives_detects_the_overlap_itself():
    assert price_refresh.options_are_alternatives(_route()["options"]) is True
    assert price_refresh.options_are_alternatives(_route(sedan=(2, 6), hiace=(7, 9))["options"]) is False
    assert price_refresh.options_are_alternatives([]) is False


def test_an_unreadable_option_is_ignored_when_deciding_alternatives():
    route = _route()
    route["options"][1]["fetch_failed"] = True
    assert price_refresh.options_are_alternatives(route["options"]) is False


# ----------------------------------------------------------------------
# BUG 2 - per vehicle vs per pax
# ----------------------------------------------------------------------

def _solo_route(per_pax):
    return {
        "id": "T9", "name": "Cairo - Faiyum", "kind": None, "price_per_pax": per_pax,
        "currency": "USD",
        "options": [{"code": "Solo", "min_pax": 1, "max_pax": 1, "unit_price": 100.0,
                     "name": "Solo", "raw": {"code": "Solo", "prices": []}}],
        "raw": {"id": "T9", "pricePerPax": per_pax, "vehiclePrice": 0.0 if per_pax else 100.0,
                "baseAdultPrice": 100.0 if per_pax else 0.0,
                "baseChildrenPrice": 0.0, "baseInfantPrice": 0.0},
    }


def test_a_per_person_minimum_party_rate_is_still_multiplied_for_the_solo_bracket():
    # The confirmed real case this rule exists for (product owner, "HRG Airport to El Quseir",
    # "Private Transfer p.p. valid for (Min.2 pax)" at 32 -> the 1-pax bracket is 64). Unchanged.
    assert _changes(_solo_route(per_pax=True), _finding([(2, 4, 45.0)], minimum_pax=2)) == \
        {"Solo": 90.0}


def test_a_per_vehicle_price_is_never_multiplied_by_the_party_size():
    # "the App must understand the difference between BasePrice (per vehicle or per Pax)". A
    # vehicle costs what it costs no matter how many people ride in it - a minimum party size is
    # a capacity/booking rule, not a multiplier. Before: 45 became 90 on a per-vehicle transport.
    assert _changes(_solo_route(per_pax=False), _finding([(2, 4, 45.0)], minimum_pax=2)) == {}


def test_bracket_price_for_takes_the_basis_explicitly_and_defaults_to_per_person():
    # Every caller written before this change means per-person, so that must stay the default.
    finding = _finding([(2, 4, 45.0)], minimum_pax=2)
    assert price_refresh.bracket_price_for(finding, 1, 1, 2) == 90.0
    assert price_refresh.bracket_price_for(finding, 1, 1, 2, price_per_pax=True) == 90.0
    assert price_refresh.bracket_price_for(finding, 1, 1, 2, price_per_pax=False) is None


# ----------------------------------------------------------------------
# FEATURE - the modality chooser
# ----------------------------------------------------------------------

def test_modality_groups_lists_each_distinct_bracket_with_a_recognisable_label():
    groups = price_refresh.modality_groups([_route(), _route()])
    assert [(g["min_pax"], g["max_pax"]) for g in groups] == [(1, 8), (1, 3)]  # widest first
    assert groups[0]["route_count"] == 2
    assert groups[0]["codes"] == ["Hiace"]
    assert "1-8 pax" in groups[0]["label"] and "Hiace" in groups[0]["label"]


def test_modality_groups_groups_by_bracket_not_by_code():
    # Real option codes are not consistent across a supplier ("ASWHRG", "PraslinLaDigue12", codes
    # equal to the transport's own name) - the passenger range is the only stable key.
    a, b = _route(), _route()
    b["options"][0]["code"] = "ASWHRG"
    b["options"][1]["code"] = "PraslinLaDigue12"
    groups = price_refresh.modality_groups([a, b])
    assert len(groups) == 2
    assert set(groups[0]["codes"]) == {"Hiace", "PraslinLaDigue12"}


def test_modality_groups_skips_options_that_could_not_be_read():
    route = _route()
    route["options"][1]["fetch_failed"] = True
    assert [(g["min_pax"], g["max_pax"]) for g in price_refresh.modality_groups([route])] == [(1, 3)]


def test_scoping_to_one_modality_leaves_every_other_one_completely_alone():
    # The human has said "this sheet is the Hiace one". Even a document bracket that would
    # otherwise match Sedan exactly must not touch it.
    route = _route()
    assert _changes(route, _finding([(1, 3, 95.0), (1, 8, 120.0)]),
                    scoped_brackets=[(1, 8)]) == {"Hiace": 120.0}


def test_an_explicit_scope_overrides_the_bracket_heuristic_inside_that_scope():
    # Product owner's own framing: an explicit "this is the Hiace sheet" is a better answer than
    # any bracket-number rule, so inside the chosen scope the exact-match-only restriction
    # relaxes back to overlap - a 1-3 line on a Hiace-scoped round still prices Hiace.
    route = _route()
    assert _changes(route, _finding([(1, 3, 95.0)]), scoped_brackets=[(1, 8)]) == {"Hiace": 95.0}


def test_no_scope_means_every_modality_exactly_as_before():
    route = _route()
    assert _changes(route, _finding([(1, 3, 95.0), (1, 8, 120.0)]), scoped_brackets=None) == \
        {"Sedan": 95.0, "Hiace": 120.0}


def test_a_scoped_out_modality_is_not_counted_as_missing_either():
    # It is simply not part of this round - not a bracket the document "failed" to price, which
    # would wrongly push the route into the "not found in the document" bucket.
    proposals = price_refresh.build_proposals([_route()], {0: _finding([(1, 8, 120.0)])},
                                              scoped_brackets=[(1, 8)])
    assert proposals[0]["missing"] == 0
    assert proposals[0]["status"] == "changed"
