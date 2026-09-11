"""Regression tests for the 2026-09-11 fix to price_refresh.rebuild_prices: a real bulk Apply
against 13 FTS-matched Transports (a Hiace-only price-refresh round - see
test_2026_09_11_price_refresh_fts_matrix_bypass.py) succeeded on the parent record (after the
airlineCode fix - see test_2026_09_11_price_refresh_transport_full_record_fetch.py) but then
failed all 13 option updates with:

    {"error":["Bean Validation constraint(s) violated on callback event:'prePersist'. Errors:
    TransportContractPrice.startDate:must not be null, TransportContractPrice.endDate:must not
    be null"],"status":"BAD_REQUEST"}

Two compounding bugs, both fixed here:

1. rebuild_prices used to always pick the WIDEST bracket as the transport's "base" modality and
   express every OTHER option as a supplement against it. That is wrong for FTS's Sedan/Hiace
   structure, where Sedan (the NARROWER 1-3 pax bracket) is deliberately the base (see
   fts_transfer_matrix.py's own module docstring and builder.build_transport_payloads'
   force_base_occupancy=FTS_SEDAN_BRACKET override at creation time). Recomputing "widest wins"
   on every refresh silently flipped which option carries the supplement - Sedan (previously
   supplement-free) suddenly needed a brand-new price entry on a run that was only ever meant to
   touch Hiace's own price. Fixed: price_refresh._current_base_option now keeps whichever
   option is ALREADY the live base (a genuine "no supplement" option) instead of re-deriving it
   from bracket width, falling back to the old widest-bracket rule only when that's ambiguous.

2. Independently, building a brand-new price entry (an option that never carried a supplement
   before) never set startDate/endDate at all - Travel Compositor's TransportContractPrice
   requires both non-null on write. Fixed: the parent's own startDate/endDate (never touched by
   this flow - see the Apply screen's own promise "validity dates stay as they are") are now
   carried into a newly-created price entry, with schemas.ContractTransportOptionPriceVO's own
   "2049-12-31" default as the last-resort fallback for endDate.
"""
import price_refresh

# CONFIRMED FINAL TRANSPORT PRICE-STRUCTURE MODEL (product owner, 2026-09-11, "yes build it now
# please"): rebuild_prices no longer has ANY auto-detect fallback for which modality is base -
# a route with two or more live brackets and no base_bracket_override is now a hard refusal
# (see build_proposals' own comment for the full transcript reference). Its signature also
# changed from a flat {code: new_price} dict to the SAME "changes" list build_proposals produces,
# each entry carrying its own write_kind ("vehicle" or "supplement"). Every test below is
# rewritten to that shape - explicit designation, one field written per round.


def _change(code, min_pax, max_pax, new_price, write_kind, start_date=None, end_date=None):
    return {"code": code, "min_pax": min_pax, "max_pax": max_pax, "old": None, "new": new_price,
            "name": code, "write_kind": write_kind, "start_date": start_date, "end_date": end_date}


def _option(code, min_pax, max_pax, unit_price, supplement=None):
    prices = [] if supplement is None else [{"adultPriceSupplement": supplement}]
    return {"code": code, "min_pax": min_pax, "max_pax": max_pax, "unit_price": unit_price,
            "name": code, "raw": {"code": code, "prices": prices}}


def _fts_style_route(sedan_supplement=None, hiace_supplement=40.0, parent_dates=True):
    parent = {"id": "T1", "pricePerPax": False, "vehiclePrice": 100.0,
              "baseAdultPrice": 0.0, "baseChildrenPrice": 0.0, "baseInfantPrice": 0.0}
    if parent_dates:
        parent["startDate"] = "2026-01-01"
        parent["endDate"] = "2099-12-31"
    return {
        "id": "T1", "name": "Cairo - Luxor", "kind": None, "price_per_pax": False,
        "options": [
            _option("Sedan", 1, 3, 100.0, supplement=sedan_supplement),
            _option("Hiace", 1, 8, 140.0, supplement=hiace_supplement),
        ],
        "raw": parent,
    }


# ----------------------------------------------------------------------
# _current_base_option / rebuild_prices - base preserved, not re-derived from bracket width
# ----------------------------------------------------------------------

def test_a_hiace_only_round_keeps_sedan_as_base_not_the_wider_hiace_bracket():
    # This is the exact real-world shape: Sedan is the DESIGNATED base, Hiace already carries a
    # supplement. Only Hiace's price is being refreshed this round.
    route = _fts_style_route()
    route["base_bracket_override"] = (1, 3)
    changes = [_change("Hiace", 1, 8, 150.0, "supplement")]
    payloads = price_refresh.rebuild_prices(route, changes)
    assert payloads["transport"]["vehiclePrice"] == 100.0  # Sedan's own unchanged price, still base
    by_code = {o["code"]: o for o in payloads["options"]}
    assert by_code["Hiace"]["payload"]["prices"][0]["adultPriceSupplement"] == round(150.0 - 100.0, 2)
    # Sedan must NOT be part of this round's payloads at all - not read, computed, or written.
    assert "Sedan" not in by_code


def test_a_sedan_only_round_keeps_sedan_as_base_and_only_touches_sedan():
    route = _fts_style_route()
    route["base_bracket_override"] = (1, 3)
    changes = [_change("Sedan", 1, 3, 110.0, "vehicle")]
    payloads = price_refresh.rebuild_prices(route, changes)
    assert payloads["transport"]["vehiclePrice"] == 110.0
    # A vehicle round writes ONLY the parent's base field - Hiace's own (untouched) supplement
    # is never read, recomputed, or written (product owner, 2026-09-11: "why should the app
    # touches even the Hiace price supplement?").
    assert payloads["options"] == []


def test_two_or_more_brackets_with_no_designation_is_a_hard_refusal():
    # CONFIRMED FINAL TRANSPORT PRICE-STRUCTURE MODEL (product owner, 2026-09-11): no more
    # auto-detect fallback at all - an ambiguous or undesignated route refuses outright rather
    # than guessing from the live read (which is exactly the read Travel Compositor's own API
    # can get wrong - see _current_base_option's own docstring).
    route = _fts_style_route(sedan_supplement=5.0, hiace_supplement=40.0)
    changes = [_change("Sedan", 1, 3, 110.0, "vehicle")]
    payloads = price_refresh.rebuild_prices(route, changes)
    assert payloads["transport"] is None
    assert "designated" in payloads["blocked"]


# ----------------------------------------------------------------------
# rebuild_prices - a brand-new price entry gets real startDate/endDate, never null
# ----------------------------------------------------------------------

def test_a_brand_new_price_entry_carries_the_parents_own_validity_dates():
    # Sedan currently has NO price entry (prices=[]) - a supplement round that touches it for the
    # first time must not come back dateless. Hiace is the designated base this round.
    route = _fts_style_route(sedan_supplement=5.0, hiace_supplement=40.0)
    route["base_bracket_override"] = (1, 8)  # Hiace designated as base
    changes = [_change("Sedan", 1, 3, 200.0, "supplement")]
    payloads = price_refresh.rebuild_prices(route, changes)
    by_code = {o["code"]: o for o in payloads["options"]}
    sedan_price_entry = by_code["Sedan"]["payload"]["prices"][0]
    assert sedan_price_entry["startDate"] == "2026-01-01"
    assert sedan_price_entry["endDate"] == "2099-12-31"


def test_a_brand_new_price_entry_falls_back_to_2049_end_date_when_the_parent_has_none():
    route = _fts_style_route(sedan_supplement=5.0, hiace_supplement=40.0, parent_dates=False)
    route["base_bracket_override"] = (1, 8)
    changes = [_change("Sedan", 1, 3, 200.0, "supplement")]
    payloads = price_refresh.rebuild_prices(route, changes)
    by_code = {o["code"]: o for o in payloads["options"]}
    sedan_price_entry = by_code["Sedan"]["payload"]["prices"][0]
    assert sedan_price_entry["startDate"] == ""  # honest last resort, not invented
    assert sedan_price_entry["endDate"] == "2049-12-31"  # schemas.ContractTransportOptionPriceVO's own default


def test_an_existing_price_entrys_own_dates_are_never_overwritten():
    route = _fts_style_route(sedan_supplement=None, hiace_supplement=40.0)
    route["base_bracket_override"] = (1, 3)  # Sedan (zero supplement) designated as base
    route["options"][1]["raw"]["prices"][0]["startDate"] = "2020-05-01"
    route["options"][1]["raw"]["prices"][0]["endDate"] = "2030-05-01"
    changes = [_change("Hiace", 1, 8, 150.0, "supplement")]
    payloads = price_refresh.rebuild_prices(route, changes)
    by_code = {o["code"]: o for o in payloads["options"]}
    hiace_price_entry = by_code["Hiace"]["payload"]["prices"][0]
    assert hiace_price_entry["startDate"] == "2020-05-01"
    assert hiace_price_entry["endDate"] == "2030-05-01"


# ----------------------------------------------------------------------
# rebuild_prices - the payload's own 'code' is always pinned to the option we trust, never left
# to whatever the raw GET response happened to carry (see api_client.update_transport_option's
# own docstring: there is no option code in the PUT url, only in the payload body)
# ----------------------------------------------------------------------

def test_each_options_payload_code_is_pinned_even_when_the_raw_get_body_lacks_one():
    route = _fts_style_route()
    route["base_bracket_override"] = (1, 3)
    route["options"][0]["raw"].pop("code", None)  # Sedan's raw GET body has no 'code' at all
    route["options"][1]["raw"].pop("code", None)
    changes = [_change("Hiace", 1, 8, 150.0, "supplement")]
    payloads = price_refresh.rebuild_prices(route, changes)
    by_code = {o["code"]: o for o in payloads["options"]}
    assert by_code["Hiace"]["payload"]["code"] == "Hiace"


def test_each_options_payload_code_is_corrected_even_when_the_raw_get_body_has_a_wrong_one():
    # A real hazard this closes: if Travel Compositor's own GET response for one option ever
    # echoed back the WRONG (or a duplicate) code, blindly forwarding it into the PUT payload
    # could silently overwrite a DIFFERENT modality instead. Pinning it to the code this
    # function already trusts (the one used to fetch this exact option) removes that risk
    # entirely, regardless of what the raw body says.
    route = _fts_style_route()
    route["base_bracket_override"] = (1, 3)
    route["options"][1]["raw"]["code"] = "Sedan"  # wrong/duplicate - should never be trusted
    changes = [_change("Hiace", 1, 8, 150.0, "supplement")]
    payloads = price_refresh.rebuild_prices(route, changes)
    by_code = {o["code"]: o for o in payloads["options"]}
    assert by_code["Hiace"]["payload"]["code"] == "Hiace"
