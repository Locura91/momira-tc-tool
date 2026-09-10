"""Tests for the bulk dated price supplement feature (product owner, 2026-09-09):

    "for a bulk action in the TRANSPORT for a specific supplier, I must add in all TRANSPORT a
    price supplement for a specific time, like christmas an new years eve period and easter.
    The app must be able to add a specific price supplement for a defined period. AND the App
    must calculate price supplement by: (Transport Price+Already existing Price supplement)*x%
    amount what human defines OR a x Number what also the human defines."

TWO product types are covered:

  - Transport: ContractTransportVO has no supplements field at all - pricing is per Option
    (occupancy bracket), each carrying a dated `prices` list that is ADDITIVE on top of the
    parent's base*Price fields (confirmed real semantics, see ContractTransportOptionPriceVO's
    own docstring). _plan_transport_supplement/_apply_transport_supplement add one new dated
    entry per bracket, computed by the app since Transport has no native PERCENT type.

  - Transfer: already had a bulk "Supplement" structured target (build_transfer_supplement_vos),
    but it only ever sent the human's raw amount/type straight to Travel Compositor, whose own
    PERCENT semantics apply only to the base price - never to price+existing-supplement
    together. The new `compute_from_current_price` flag makes the APP do that compounding
    math (via _existing_transfer_supplement_total) and always write the result as ABSOLUTE.

Both paths follow the same "preview everything before writing" architecture as the rest of
bulk_notes.py - see that module's own docstring.
"""
import bulk_notes


# ---------------------------------------------------------------------------
# Fake client - just enough of the real api_client.TravelCompositorAPI surface
# for get_transports/get_transport/get_transport_option/update_transport_option
# and get_transfers/update_transfer to exercise the new logic end-to-end.
# ---------------------------------------------------------------------------
class _FakeClient:
    def __init__(self, transports=None, transport_options=None, transfers=None):
        self._transports = transports or []
        self._options = transport_options or {}  # (transport_id, code) -> option dict
        self._transfers = transfers or []
        self.updated_options = []
        self.updated_transfers = []

    def get_transports(self, supplier_id):
        return {"transport": self._transports}

    def get_transport(self, supplier_id, transport_id):
        return next(t for t in self._transports if t["id"] == transport_id)

    def get_transport_option(self, supplier_id, transport_id, code):
        return self._options[(transport_id, code)]

    def update_transport_option(self, supplier_id, transport_id, payload):
        self.updated_options.append((transport_id, payload))
        return payload

    def get_transfers(self, supplier_id):
        return {"transfer": self._transfers}

    def update_transfer(self, supplier_id, payload):
        self.updated_transfers.append(payload)
        return payload


def _transport(t_id="TRANSPORT-1", name="Airport Transfer CAI", codes=("OPT1",),
               base_adult=100.0, base_children=50.0, base_infant=0.0):
    return {
        "id": t_id, "name": name, "optionCodes": list(codes),
        "baseAdultPrice": base_adult, "baseChildrenPrice": base_children,
        "baseInfantPrice": base_infant, "currency": "EUR",
    }


def _option(code="OPT1", min_p=1, max_p=4, prices=None):
    return {"code": code, "minPassengers": min_p, "maxPassengers": max_p,
            "prices": prices if prices is not None else []}


# ---------------------------------------------------------------------------
# Transport: plan
# ---------------------------------------------------------------------------

def test_transport_supplement_requires_a_name_and_dates():
    client = _FakeClient()
    result = bulk_notes._plan_transport_supplement(client, "SUP1", "", "2026-12-20", "2027-01-05",
                                                    False, 10.0)
    assert result["error"]
    result2 = bulk_notes._plan_transport_supplement(client, "SUP1", "Christmas", "", "", False, 10.0)
    assert result2["error"]


def test_transport_fixed_amount_applies_the_same_number_to_every_price_field():
    t = _transport()
    opt = _option()
    client = _FakeClient(transports=[t], transport_options={("TRANSPORT-1", "OPT1"): opt})
    result = bulk_notes._plan_transport_supplement(
        client, "SUP1", "Christmas Surcharge", "2026-12-20", "2027-01-05", False, 25.0)
    assert result["will_change"] == 1
    item = result["items"][0]
    new_entry = item["record"]["prices"][-1]
    assert new_entry["adultPriceSupplement"] == 25.0
    assert new_entry["childrenPriceSupplement"] == 25.0
    assert new_entry["startDate"] == "2026-12-20"
    assert new_entry["endDate"] == "2027-01-05"
    assert new_entry["name"] == "Christmas Surcharge"


def _entry_named(prices, name):
    return next(e for e in prices if e.get("name") == name)


def test_transport_percent_compounds_base_price_with_the_currently_active_supplement():
    # A bracket with base 100 and an already-active +20 supplement, 10% surcharge ->
    # (100 + 20) * 0.10 = 12, NOT 100 * 0.10 = 10 (which would ignore the existing supplement -
    # exactly the gap the product owner's formula closes).
    t = _transport(base_adult=100.0, base_children=0.0, base_infant=0.0)
    opt = _option(prices=[{
        "name": "Standing rate", "startDate": "2026-01-01", "endDate": "2049-12-31",
        "adultPriceSupplement": 20.0, "childrenPriceSupplement": 0.0, "infantPriceSupplement": 0.0,
    }])
    client = _FakeClient(transports=[t], transport_options={("TRANSPORT-1", "OPT1"): opt})
    result = bulk_notes._plan_transport_supplement(
        client, "SUP1", "NYE Surcharge", "2026-12-31", "2027-01-01", True, 10.0)
    new_entry = _entry_named(result["items"][0]["record"]["prices"], "NYE Surcharge")
    assert new_entry["adultPriceSupplement"] == 12.0


def test_transport_percent_is_computed_per_bracket_not_shared_across_brackets():
    # CONFIRMED (schemas.py): different brackets' supplements do not scale together (a real
    # 4-bracket example showed 64/43/64/43) - two brackets with different base prices must get
    # independently-computed amounts, never one bracket's % reused for the other.
    t = _transport(codes=("OPT1", "OPT2"), base_adult=100.0)
    opt1 = _option(code="OPT1", min_p=1, max_p=2)
    opt2 = _option(code="OPT2", min_p=3, max_p=4)
    client = _FakeClient(transports=[t], transport_options={
        ("TRANSPORT-1", "OPT1"): opt1, ("TRANSPORT-1", "OPT2"): opt2,
    })
    result = bulk_notes._plan_transport_supplement(
        client, "SUP1", "Easter Surcharge", "2027-04-01", "2027-04-10", True, 15.0)
    assert result["will_change"] == 2
    for item in result["items"]:
        new_entry = item["record"]["prices"][-1]
        assert new_entry["adultPriceSupplement"] == 15.0  # both brackets share base=100 here


def test_transport_supplement_splits_the_standing_entry_around_the_peak_period():
    # CORRECTED (2026-09-10, real product-owner report): the original 2026-09-09 version of
    # this feature blindly APPENDED the peak entry on top of the standing one, so both covered
    # the peak dates at once - an overlap Travel Compositor's own UI never produces. The
    # confirmed correct process: truncate the standing entry to end the day before the peak
    # starts, insert the peak entry, and insert a fresh "resume" entry (same supplement as the
    # standing one) for the day after the peak ends through the standing entry's own end date.
    t = _transport()
    existing_entry = {"name": "Standing rate", "startDate": "2026-01-01", "endDate": "2049-12-31",
                      "adultPriceSupplement": 20.0, "childrenPriceSupplement": 0.0,
                      "infantPriceSupplement": 0.0}
    opt = _option(prices=[existing_entry])
    client = _FakeClient(transports=[t], transport_options={("TRANSPORT-1", "OPT1"): opt})
    result = bulk_notes._plan_transport_supplement(
        client, "SUP1", "Christmas Surcharge", "2026-12-20", "2027-01-05", False, 30.0)
    new_prices = result["items"][0]["record"]["prices"]
    # Three non-overlapping entries now: before, peak, resume-after.
    assert len(new_prices) == 3
    by_start = sorted(new_prices, key=lambda e: e["startDate"])
    before, peak, after = by_start
    assert before["startDate"] == "2026-01-01" and before["endDate"] == "2026-12-19"
    assert before["adultPriceSupplement"] == 20.0  # standing rate, unchanged
    assert peak["startDate"] == "2026-12-20" and peak["endDate"] == "2027-01-05"
    assert peak["adultPriceSupplement"] == 30.0
    assert peak["name"] == "Christmas Surcharge"
    assert after["startDate"] == "2027-01-06" and after["endDate"] == "2049-12-31"
    assert after["adultPriceSupplement"] == 20.0  # back to the standing rate, unchanged
    # No two entries cover the same day.
    spans = sorted((e["startDate"], e["endDate"]) for e in new_prices)
    for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
        assert e1 < s2


def test_sending_the_same_named_supplement_for_the_same_dates_twice_is_a_no_op():
    t = _transport()
    already_there = {"name": "Christmas Surcharge", "startDate": "2026-12-20", "endDate": "2027-01-05",
                     "adultPriceSupplement": 30.0, "childrenPriceSupplement": 30.0,
                     "infantPriceSupplement": 30.0}
    opt = _option(prices=[already_there])
    client = _FakeClient(transports=[t], transport_options={("TRANSPORT-1", "OPT1"): opt})
    result = bulk_notes._plan_transport_supplement(
        client, "SUP1", "Christmas Surcharge", "2026-12-20", "2027-01-05", False, 30.0)
    assert result["will_change"] == 0
    assert result["unchanged"] == 1


# ---------------------------------------------------------------------------
# Transport: _carve_transport_option_price_window - the split/carve logic directly
# ---------------------------------------------------------------------------

_NEW_FIELDS = {"adultPriceSupplement": 30.0, "childrenPriceSupplement": 15.0,
              "infantPriceSupplement": 0.0}


def test_carve_with_no_existing_entries_just_inserts_the_peak_entry():
    # A bracket priced exactly at base (no supplement at all, per
    # ContractTransportOptionPriceVO's own confirmed convention of "no entries = base rate")
    # needs no before/after entries - absence of an entry already means "no supplement".
    result = bulk_notes._carve_transport_option_price_window(
        [], "2026-12-20", "2027-01-05", _NEW_FIELDS, "Christmas Surcharge")
    assert len(result) == 1
    assert result[0]["startDate"] == "2026-12-20" and result[0]["endDate"] == "2027-01-05"
    assert result[0]["adultPriceSupplement"] == 30.0


def test_carve_entry_entirely_before_the_peak_is_untouched():
    entries = [{"name": "Old", "startDate": "2020-01-01", "endDate": "2026-01-01",
               "adultPriceSupplement": 5.0}]
    result = bulk_notes._carve_transport_option_price_window(
        entries, "2026-12-20", "2027-01-05", _NEW_FIELDS, "Christmas Surcharge")
    assert entries[0] in result  # byte-for-byte unchanged
    assert len(result) == 2


def test_carve_entry_entirely_after_the_peak_is_untouched():
    entries = [{"name": "Later", "startDate": "2027-06-01", "endDate": "2049-12-31",
               "adultPriceSupplement": 5.0}]
    result = bulk_notes._carve_transport_option_price_window(
        entries, "2026-12-20", "2027-01-05", _NEW_FIELDS, "Christmas Surcharge")
    assert entries[0] in result
    assert len(result) == 2


def test_carve_entry_entirely_inside_the_peak_window_is_superseded_not_kept():
    # A leftover from an earlier, narrower run for the same season - fully replaced by the new
    # peak entry rather than left dangling as a redundant/overlapping third entry.
    entries = [{"name": "Old smaller peak", "startDate": "2026-12-24", "endDate": "2026-12-26",
               "adultPriceSupplement": 999.0}]
    result = bulk_notes._carve_transport_option_price_window(
        entries, "2026-12-20", "2027-01-05", _NEW_FIELDS, "Christmas Surcharge")
    assert len(result) == 1
    assert result[0]["adultPriceSupplement"] == 30.0
    assert "999.0" not in str(result)


def test_carve_is_idempotent_rerunning_for_the_exact_same_window_replaces_not_duplicates():
    entries = [{"name": "Standing rate", "startDate": "2026-01-01", "endDate": "2049-12-31",
               "adultPriceSupplement": 20.0}]
    first = bulk_notes._carve_transport_option_price_window(
        entries, "2026-12-20", "2027-01-05", _NEW_FIELDS, "Christmas Surcharge")
    second = bulk_notes._carve_transport_option_price_window(
        first, "2026-12-20", "2027-01-05", _NEW_FIELDS, "Christmas Surcharge")
    assert len(second) == 3  # still before/peak/after, not a growing pile
    assert len([e for e in second if e["name"] == "Christmas Surcharge"]) == 1


def test_carve_handles_a_peak_window_starting_exactly_on_the_standing_entrys_start_date():
    # e_start == start_date: no "before" remainder should be created (it would be degenerate -
    # ending before it begins).
    entries = [{"name": "Standing rate", "startDate": "2026-12-20", "endDate": "2049-12-31",
               "adultPriceSupplement": 20.0}]
    result = bulk_notes._carve_transport_option_price_window(
        entries, "2026-12-20", "2027-01-05", _NEW_FIELDS, "Christmas Surcharge")
    assert len(result) == 2  # peak + after only, no degenerate before-piece
    assert all(e["startDate"] != "" for e in result)


def test_carve_handles_two_peak_windows_across_different_years_without_interference():
    # Christmas this year, Easter next year - two independent carves against the same
    # progressively-updated entries list, exactly how a human would run this tool twice.
    entries = [{"name": "Standing rate", "startDate": "2026-01-01", "endDate": "2049-12-31",
               "adultPriceSupplement": 20.0}]
    after_christmas = bulk_notes._carve_transport_option_price_window(
        entries, "2026-12-20", "2027-01-05", _NEW_FIELDS, "Christmas Surcharge")
    after_easter = bulk_notes._carve_transport_option_price_window(
        after_christmas, "2027-04-01", "2027-04-10", _NEW_FIELDS, "Easter Surcharge")
    names = sorted(e["name"] for e in after_easter)
    # The Christmas carve leaves a "Standing rate" tail (Jan 6 - Dec 31); the Easter carve then
    # splits THAT tail around itself too, so "Standing rate" appears three times in total: the
    # original pre-Christmas piece, plus the two pieces the Easter carve makes from the tail.
    assert names == ["Christmas Surcharge", "Easter Surcharge",
                     "Standing rate", "Standing rate", "Standing rate"]
    # No two entries overlap anywhere in the final list.
    spans = sorted((e["startDate"], e["endDate"]) for e in after_easter)
    for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
        assert e1 < s2


def test_transport_supplement_anchors_the_existing_rate_to_the_periods_start_date_not_today():
    # A future surcharge planned well ahead of time must compound onto the rate that will
    # actually be in effect when the peak period BEGINS, not whatever happens to be active
    # today (the date this tool is run).
    t = _transport(base_adult=100.0, base_children=0.0, base_infant=0.0)
    opt = _option(prices=[
        {"name": "Off-season rate", "startDate": "2020-01-01", "endDate": "2026-11-30",
         "adultPriceSupplement": 0.0, "childrenPriceSupplement": 0.0, "infantPriceSupplement": 0.0},
        {"name": "High season rate", "startDate": "2026-12-01", "endDate": "2049-12-31",
         "adultPriceSupplement": 20.0, "childrenPriceSupplement": 0.0, "infantPriceSupplement": 0.0},
    ])
    client = _FakeClient(transports=[t], transport_options={("TRANSPORT-1", "OPT1"): opt})
    # "Today" (whatever it actually is when the test runs) is irrelevant here - what matters is
    # that 2026-12-20 (the peak start) falls inside the High season rate, not Off-season.
    result = bulk_notes._plan_transport_supplement(
        client, "SUP1", "NYE Surcharge", "2026-12-20", "2027-01-05", True, 10.0)
    peak = _entry_named(result["items"][0]["record"]["prices"], "NYE Surcharge")
    assert peak["adultPriceSupplement"] == 12.0  # (100 + 20) * 10%, not (100 + 0) * 10%


# ---------------------------------------------------------------------------
# Transport: apply
# ---------------------------------------------------------------------------

def test_transport_apply_writes_via_update_transport_option_with_the_parent_id():
    t = _transport()
    opt = _option()
    client = _FakeClient(transports=[t], transport_options={("TRANSPORT-1", "OPT1"): opt})
    plan = bulk_notes._plan_transport_supplement(
        client, "SUP1", "Christmas Surcharge", "2026-12-20", "2027-01-05", False, 30.0)
    result = bulk_notes.apply(client, "SUP1", plan)
    assert len(result["updated"]) == 1
    assert client.updated_options[0][0] == "TRANSPORT-1"
    assert client.updated_options[0][1]["prices"][-1]["adultPriceSupplement"] == 30.0


def test_apply_dispatches_transport_supplement_kind_to_the_option_level_writer():
    # apply() must recognize kind == "transport_supplement" and route to
    # _apply_transport_supplement rather than the generic update_fn(supplier_id, record) path,
    # which would call update_transport(supplier_id, <option payload>) - wrong endpoint, wrong
    # shape entirely.
    t = _transport()
    opt = _option()
    client = _FakeClient(transports=[t], transport_options={("TRANSPORT-1", "OPT1"): opt})
    plan = bulk_notes.plan_structured(
        client, "SUP1", "Transport", "transport_supplement",
        {"name": "X", "start_date": "2026-12-20", "end_date": "2027-01-05",
         "is_percent": False, "amount": 10.0})
    bulk_notes.apply(client, "SUP1", plan)
    assert len(client.updated_options) == 1
    assert len(client.updated_transfers) == 0


# ---------------------------------------------------------------------------
# Transfer: compute_from_current_price
# ---------------------------------------------------------------------------

def _transfer(t_id="TRANSFER-1", base_price=200.0, supplements=None):
    return {
        "id": t_id, "name": "Hurghada Airport Transfer", "startDate": "2026-01-01",
        "endDate": "2049-12-31", "currency": "EUR", "basePrice": base_price,
        "supplements": supplements or [],
    }


def test_transfer_existing_supplement_total_sums_only_currently_active_absolute_entries():
    record = _transfer(base_price=200.0, supplements=[
        {"name": "Old", "amount": 30.0, "type": "ABSOLUTE", "active": True,
         "startDate": "2020-01-01", "endDate": "2049-12-31"},
        {"name": "Expired", "amount": 999.0, "type": "ABSOLUTE", "active": True,
         "startDate": "2020-01-01", "endDate": "2021-01-01"},
        {"name": "Inactive", "amount": 999.0, "type": "ABSOLUTE", "active": False,
         "startDate": "2020-01-01", "endDate": "2049-12-31"},
    ])
    total = bulk_notes._existing_transfer_supplement_total(record, today="2026-06-01")
    assert total == 30.0


def test_transfer_existing_supplement_total_converts_percent_entries_using_base_price():
    record = _transfer(base_price=200.0, supplements=[
        {"name": "Night fee", "amount": 10.0, "type": "PERCENT", "active": True,
         "startDate": "2020-01-01", "endDate": "2049-12-31"},
    ])
    total = bulk_notes._existing_transfer_supplement_total(record, today="2026-06-01")
    assert total == 20.0  # 200 * 10%


def test_transfer_compute_from_current_price_percent_compounds_base_and_existing_supplement():
    record = _transfer(base_price=200.0, supplements=[
        {"name": "Old", "amount": 20.0, "type": "ABSOLUTE", "active": True,
         "startDate": "2020-01-01", "endDate": "2049-12-31"},
    ])
    entry = bulk_notes._build_structured_entry(
        "transfer_supplement",
        {"name": "Christmas Surcharge", "compute_from_current_price": True, "is_percent": True,
         "amount": 10.0, "start_date": "2026-12-20", "end_date": "2027-01-05"},
        record)
    assert entry is not None
    # (200 + 20) * 10% = 22, always written as ABSOLUTE (never TC's own PERCENT semantics,
    # which would ignore the existing +20 and apply 10% to 200 alone).
    assert entry["type"] == "ABSOLUTE"
    assert entry["amount"] == 22.0
    assert entry["startDate"] == "2026-12-20"
    assert entry["endDate"] == "2027-01-05"


def test_transfer_compute_from_current_price_fixed_amount_is_unchanged():
    record = _transfer(base_price=200.0)
    entry = bulk_notes._build_structured_entry(
        "transfer_supplement",
        {"name": "NYE Surcharge", "compute_from_current_price": True, "is_percent": False,
         "amount": 45.0, "start_date": "2026-12-31", "end_date": "2027-01-01"},
        record)
    assert entry["type"] == "ABSOLUTE"
    assert entry["amount"] == 45.0


def test_transfer_supplement_without_compute_flag_behaves_exactly_as_before():
    # Backward compatibility: compute_from_current_price defaults to falsy/absent, so an
    # existing caller (or a human leaving the new checkbox unticked) gets the raw amount/type
    # sent straight through, unchanged from before this feature existed.
    record = _transfer(base_price=200.0)
    entry = bulk_notes._build_structured_entry(
        "transfer_supplement",
        {"name": "Manual PERCENT", "amount": 50.0, "type": "PERCENT"},
        record)
    assert entry["type"] == "PERCENT"
    assert entry["amount"] == 50.0
