"""Regression tests for the 2026-09-11 rewrite of how price_refresh.lookup_prices_from_fts_matrix
matches a live Transport to a cell of FTS's rate matrix.

REAL REPORT (product owner, running a real bulk price refresh with only the Sedan CSV, verbatim):
"the bulk update for transport is absolutely not working. I checked now multiple times, and the
App gives me Transports, which are not on the list and it misses out on the price errors. Example
Transport from Marsa Matruh to Siwa and the price was not detected by the App."

ROOT CAUSE (measured against the product owner's own 21x21 Sedan export, 271 priced cells): the
old code built a fake "existing transports" list - one entry per priced cell, named
f"{origin} - {arrival}" - and picked the single best match from
transport_matcher.suggest_existing_transport_matches. That scorer averages a departure half-score
and an arrival half-score, and awards 0.9 for a plain substring hit, so ONE matching endpoint plus
a meaningless fuzzy score on the other still cleared the 0.5 floor. Measured damage on the real
file:

  * 18 of 271 priced routes got the WRONG price - every same-city route (live "Hurghada -
    Hurghada", genuinely $30) was matched to "Cairo - Hurghada" ($140).
  * ALL 170 unpriced pairs (the "—" and "Train" cells) were matched to some OTHER cell's price -
    live "Cairo - Luxor", which FTS deliberately sells as train-only, came back priced $135 from
    "El Gouna - Cairo".
  * 7 of 8 sampled routes with an endpoint the sheet doesn't list at all still matched - live
    "Cairo - Alexandria" came back at $155 from "Cairo - Makadi Bay".

Those wrong-cell matches ARE the "Transports, which are not on the list" half of the report: not
merely spurious rows, but real prices lifted from the wrong city pair and offered for Publish.

THE FIX: the matrix is a structured grid, so each endpoint is now resolved to exactly one city on
its own (fts_transfer_matrix.match_place_to_city) and the price is read from that one exact
(origin, destination) cell - never a neighbour, never the reverse direction. A pair the sheet
doesn't price, and an endpoint that resolves ambiguously, are each reported with their own reason
instead of silently borrowing another cell's number.
"""
import csv

import pytest

import fts_transfer_matrix
import price_refresh

# The real export's own city list (see the product owner's FTS_Momira_Whole_Egypt_B2B_Catalogue
# Sedan sheet) - deliberately the REAL one, because the bugs this locks down are all about
# same-city routes and near-miss neighbours ("Marsa Alam" vs "Marsa Matruh") that only exist at
# this scale.
CITIES = ["Cairo", "Luxor", "Aswan", "Abu Simbel", "Faiyum", "El Alamein", "Marsa Matruh", "Siwa",
          "Marsa Alam", "El Quseir", "Safaga", "Soma Bay", "Makadi Bay", "Sahl Hasheesh",
          "Hurghada", "El Gouna", "Ain Sokhna", "Sharm El Sheikh", "Dahab", "Nuweiba", "Taba"]

# Only the cells these tests actually assert on; every other cell is "—" (no transfer), which is
# itself one of the cases under test.
PRICED = {
    ("Marsa Matruh", "Siwa"): 95, ("Siwa", "Marsa Matruh"): 95,
    ("Cairo", "Siwa"): 220, ("Cairo", "Cairo"): 30, ("Hurghada", "Hurghada"): 30,
    ("Cairo", "Hurghada"): 140, ("Hurghada", "El Gouna"): 30, ("Marsa Alam", "El Quseir"): 55,
    ("Cairo", "Makadi Bay"): 155, ("El Gouna", "Cairo"): 135,
}
TRAIN = {("Cairo", "Luxor"), ("Luxor", "Cairo")}


def _write_matrix(path, title, prices):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([title] + [""] * len(CITIES))
        w.writerow(["Select a season in B3. Prices are one-way net B2B USD per vehicle."])
        w.writerow(["Season", "Standard"])
        w.writerow([""])
        w.writerow(["From / To"] + CITIES)
        for origin in CITIES:
            row = [origin]
            for dest in CITIES:
                if (origin, dest) in TRAIN:
                    row.append("Train")
                elif (origin, dest) in prices:
                    row.append(f"${prices[(origin, dest)]}")
                else:
                    row.append("—")
            w.writerow(row)


@pytest.fixture
def sedan_csv(tmp_path):
    path = str(tmp_path / "FTS_Sedan.csv")
    _write_matrix(path, "TRANSFER MATRIX — SEDAN", PRICED)
    return path


@pytest.fixture
def hiace_csv(tmp_path):
    # Same grid, every price +25 - so the confirmed rule (supplement = Hiace - Sedan) always
    # nets out to exactly 25 and is trivial to assert on.
    path = str(tmp_path / "FTS_Hiace.csv")
    _write_matrix(path, "TRANSFER MATRIX — TOYOTA HIACE", {k: v + 25 for k, v in PRICED.items()})
    return path


def _live(name, sedan=(1, 3), hiace=(1, 8), sedan_price=1.0, hiace_price=2.0):
    """A live per-vehicle Transport shaped like the real TRANSPORT-418748: two modalities,
    Sedan 1-3 as the base and Hiace 1-8 carrying the supplement."""
    return {
        "id": "TRANSPORT-418748", "name": name, "departure_code": None, "arrival_code": None,
        "currency": "USD", "price_per_pax": False, "base_adult": sedan_price,
        "options": [
            {"code": "Sedan", "min_pax": sedan[0], "max_pax": sedan[1],
             "unit_price": sedan_price, "name": "Sedan", "raw": {"code": "Sedan", "prices": []}},
            {"code": "Hiace", "min_pax": hiace[0], "max_pax": hiace[1],
             "unit_price": hiace_price, "name": "Hiace",
             "raw": {"code": "Hiace", "prices": [{"adultPriceSupplement": hiace_price - sedan_price}]}},
        ],
        "raw": {"id": "TRANSPORT-418748", "pricePerPax": False, "vehiclePrice": sedan_price,
                "baseAdultPrice": 0.0, "baseChildrenPrice": 0.0, "baseInfantPrice": 0.0},
    }


def _price(route, sedan_csv=None, hiace_csv=None):
    findings, err = price_refresh.lookup_prices_from_fts_matrix(
        [route], sedan_csv_path=sedan_csv, hiace_csv_path=hiace_csv)
    assert err is None
    return findings.get(0)


# ----------------------------------------------------------------------
# fts_transfer_matrix.match_place_to_city - one endpoint, one city, or an honest None
# ----------------------------------------------------------------------

@pytest.mark.parametrize("place,expected", [
    ("Siwa", "Siwa"),
    ("Siwa Oasis", "Siwa"),                     # the real live name for this route's arrival
    ("Marsa Matruh", "Marsa Matruh"),
    ("Private Transfer Marsa Matruh", "Marsa Matruh"),  # a descriptive live name
    ("Hurghada Airport", "Hurghada"),
    ("HRG", "Hurghada"),                        # confirmed airport-code-as-city rule
    ("RMF", "Marsa Alam"),
    ("sharm el sheikh", "Sharm El Sheikh"),     # case-insensitive
    ("Sahl Hasheesh Marina", "Sahl Hasheesh"),
])
def test_a_real_place_name_resolves_to_exactly_one_city(place, expected):
    assert fts_transfer_matrix.match_place_to_city(place, CITIES)["city"] == expected


@pytest.mark.parametrize("place", ["Alexandria", "Port Ghalib", "Kom Ombo", "Giza Pyramids",
                                   "Ras Mohammed", "Dendera"])
def test_a_place_the_sheet_does_not_list_resolves_to_nothing_rather_than_the_nearest(place):
    # This is the false-positive half of the bug: "Alexandria" used to end up priced as some
    # Cairo route because the OTHER endpoint alone carried the average over the floor.
    assert fts_transfer_matrix.match_place_to_city(place, CITIES)["city"] is None


def test_an_ambiguous_place_name_is_reported_not_guessed():
    # The grid holds BOTH "Marsa Alam" and "Marsa Matruh" - ~600km apart. A live route naming
    # only "Marsa" genuinely cannot be resolved and must never be picked at random.
    result = fts_transfer_matrix.match_place_to_city("Marsa", CITIES)
    assert result["city"] is None
    assert set(result["ambiguous"]) == {"Marsa Alam", "Marsa Matruh"}


# ----------------------------------------------------------------------
# The reported route: Marsa Matruh -> Siwa, $95, must be detected
# ----------------------------------------------------------------------

def test_the_reported_marsa_matruh_to_siwa_route_is_detected_at_95(sedan_csv):
    # The exact case the product owner reported as "the price was not detected by the App", with
    # the live record's own arrival naming ("Siwa Oasis", not the sheet's bare "Siwa").
    finding = _price(_live("Marsa Matruh - Siwa Oasis", sedan_price=175.0, hiace_price=200.0),
                     sedan_csv=sedan_csv)
    assert finding["found"] is True
    assert finding["brackets"][0]["price"] == 95.0
    assert finding["only_option_code"] == "Sedan"  # Hiace left alone this round


def test_it_survives_a_descriptive_live_name_with_a_prefix(sedan_csv):
    finding = _price(_live("Private Transfer Marsa Matruh - Siwa Oasis", sedan_price=175.0),
                     sedan_csv=sedan_csv)
    assert finding["found"] is True
    assert finding["brackets"][0]["price"] == 95.0


# ----------------------------------------------------------------------
# The false positives - a wrong cell's price must never be offered
# ----------------------------------------------------------------------

def test_a_same_city_route_gets_its_own_diagonal_price_not_a_neighbours(sedan_csv):
    # Was: live "Hurghada - Hurghada" ($30 in the grid) matched "Cairo - Hurghada" and was
    # offered at $140. All 18 of the real file's same-city routes failed this way.
    finding = _price(_live("Hurghada - Hurghada", sedan_price=999.0), sedan_csv=sedan_csv)
    assert finding["found"] is True
    assert finding["brackets"][0]["price"] == 30.0


def test_a_train_only_pair_is_never_given_another_cells_price(sedan_csv):
    # Was: live "Cairo - Luxor" (Train in the sheet - a different product type entirely) came
    # back priced $135 from "El Gouna - Cairo". All 170 unpriced pairs failed this way.
    finding = _price(_live("Cairo - Luxor", sedan_price=999.0), sedan_csv=sedan_csv)
    assert finding["found"] is False
    assert finding["brackets"] == []
    assert "train" in finding["note"].lower()  # says WHY, rather than vanishing


def test_a_pair_marked_unavailable_is_reported_not_priced_from_elsewhere(sedan_csv):
    finding = _price(_live("Dahab - Taba", sedan_price=999.0), sedan_csv=sedan_csv)
    assert finding["found"] is False
    assert "no transfer available" in finding["note"]


def test_a_route_with_an_endpoint_the_sheet_never_lists_is_left_alone_entirely(sedan_csv):
    # Was: live "Cairo - Alexandria" offered at $155 from "Cairo - Makadi Bay". Now it doesn't
    # even produce a finding - it is simply a route this document doesn't cover, which is the
    # ordinary case for a supplier's non-FTS routes and must not become review-screen noise.
    findings, err = price_refresh.lookup_prices_from_fts_matrix(
        [_live("Cairo - Alexandria", sedan_price=999.0)], sedan_csv_path=sedan_csv)
    assert err is None
    assert findings == {}


def test_an_ambiguous_route_name_is_surfaced_rather_than_priced_at_random(sedan_csv):
    finding = _price(_live("Marsa - Cairo", sedan_price=999.0), sedan_csv=sedan_csv)
    assert finding["found"] is False
    assert "Marsa Alam" in finding["note"] and "Marsa Matruh" in finding["note"]


# ----------------------------------------------------------------------
# Both files together - the product owner's confirmed pricing rule, end to end
# ----------------------------------------------------------------------

def test_both_files_together_price_sedan_as_base_and_hiace_as_the_difference(sedan_csv, hiace_csv):
    # Product owner's own words: "PriceVehicle = price Sedan. Price Hiace = Price Hiace from file
    # - price from Sedan and the difference of this price is included as price supplement."
    route = _live("Marsa Matruh - Siwa Oasis", sedan_price=175.0, hiace_price=200.0)
    finding = _price(route, sedan_csv=sedan_csv, hiace_csv=hiace_csv)
    assert finding["found"] is True
    prices = {(b["min_pax"], b["max_pax"]): b["price"] for b in finding["brackets"]}
    assert prices[fts_transfer_matrix.FTS_SEDAN_BRACKET] == 95.0
    assert prices[fts_transfer_matrix.FTS_HIACE_BRACKET] == 120.0

    proposals = price_refresh.build_proposals([route], {0: finding})
    payloads = price_refresh.rebuild_prices(route, {c["code"]: c["new"]
                                                    for c in proposals[0]["changes"]})
    assert payloads["transport"]["vehiclePrice"] == 95.0        # base IS the Sedan price
    by_code = {o["code"]: o for o in payloads["options"]}
    assert by_code["Sedan"]["payload"]["prices"] == []          # base modality carries no supplement
    assert by_code["Hiace"]["payload"]["prices"][0]["adultPriceSupplement"] == 25.0  # 120 - 95
    assert by_code["Hiace"]["unit_price"] == 120.0


def test_a_pair_priced_in_only_one_of_the_two_files_still_prices_that_one_vehicle(tmp_path,
                                                                                  sedan_csv):
    # Previously a one-sided pair was dropped from a both-files round entirely (combine_fts_
    # transfer_matrix only ever emitted pairs priced in BOTH). It now prices the vehicle that
    # genuinely has a price, under the same single-bracket safety rule.
    hiace_path = str(tmp_path / "FTS_Hiace_partial.csv")
    _write_matrix(hiace_path, "TRANSFER MATRIX — TOYOTA HIACE",
                  {k: v + 25 for k, v in PRICED.items() if k != ("Marsa Matruh", "Siwa")})
    route = _live("Marsa Matruh - Siwa Oasis", sedan_price=175.0, hiace_price=200.0)
    finding = _price(route, sedan_csv=sedan_csv, hiace_csv=hiace_path)
    assert finding["found"] is True
    assert [b["price"] for b in finding["brackets"]] == [95.0]
    assert finding["only_option_code"] == "Sedan"  # Hiace untouched, not guessed at


# ----------------------------------------------------------------------
# preview_untouched_modality_effects - the shared-base side effect, made visible
# ----------------------------------------------------------------------

def test_a_sedan_only_round_names_what_happens_to_the_hiace_price():
    # UPDATED 2026-09-11 (real TRANSPORT-423015 - see apply_proposals' own comment): an untouched
    # modality's stored supplement is no longer rewritten to hold its price steady, because
    # Travel Compositor's API under-reports supplements and recomputing one from that read
    # destroys it. Its supplement is left alone, so its price moves with the shared base instead -
    # which is what the review screen must now say.
    # Real TRANSPORT-418748 numbers: Vehicle 175 (Sedan), Hiace 200. Sedan 175 -> 95 moves the
    # shared base down 80, so Hiace follows it down to 120.
    route = _live("Marsa Matruh - Siwa Oasis", sedan_price=175.0, hiace_price=200.0)
    effects = price_refresh.preview_untouched_modality_effects(
        route, [{"code": "Sedan", "min_pax": 1, "max_pax": 3, "old": 175.0, "new": 95.0}])
    assert len(effects) == 1
    assert effects[0]["code"] == "Hiace"
    assert effects[0]["price"] == 200.0
    assert effects[0]["base_delta"] == -80.0
    assert effects[0]["new_price"] == 120.0


def test_an_untouched_modalitys_price_entries_are_never_written_at_all():
    # The data-loss guard itself: apply_proposals must PUT only the options this round actually
    # reprices. A supplement the API reported as 0 but which is really 50 (the confirmed
    # TRANSPORT-423015 platform mismatch) then survives, instead of being overwritten with a
    # number derived from the phantom read.
    route = _live("Marsa Matruh - Siwa Oasis", sedan_price=175.0, hiace_price=200.0)
    written = []

    class _Client:
        def update_transport(self, supplier_id, payload):
            return {"id": payload.get("id")}

        def update_transport_option(self, supplier_id, transport_id, payload):
            written.append(payload.get("code"))
            return {"code": payload.get("code")}

    proposal = {"route": route, "accepted": True, "status": "changed", "index": 0,
                "changes": [{"code": "Sedan", "min_pax": 1, "max_pax": 3,
                             "old": 175.0, "new": 95.0}]}
    result = price_refresh.apply_proposals(_Client(), "51758", [proposal])
    assert result["failed"] == []
    assert written == ["Sedan"], "only the repriced modality may be written"


def test_no_side_effect_is_reported_when_every_modality_is_being_repriced_anyway():
    route = _live("Marsa Matruh - Siwa Oasis", sedan_price=175.0, hiace_price=200.0)
    effects = price_refresh.preview_untouched_modality_effects(route, [
        {"code": "Sedan", "min_pax": 1, "max_pax": 3, "old": 175.0, "new": 95.0},
        {"code": "Hiace", "min_pax": 1, "max_pax": 8, "old": 200.0, "new": 120.0},
    ])
    assert effects == []
