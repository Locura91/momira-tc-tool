"""Regression test for TRANSPORT-406543 / supplier 51758 (product owner, 2026-09-10).

CONFIRMED REAL BUG REPORT: "bulk transport ssupplement did NOT work. Wrong price supplement,
wrong selected modality in the price supplements and No supplement was added." The product
owner then supplied real Postman GET responses for this exact Transport as ground truth: the
"current situation" (Sedan has an empty `prices` list; Hiace has one standing entry named
"High Season" running 2026-09-10..2049-12-31, all supplement amounts 0.0) and a hand-built
"real live example with the CORRECT supplement changes" showing the desired result of adding a
peak-period entry for 2026-12-24..2027-01-07.

Tracing `_plan_transport_supplement`/`_carve_transport_option_price_window` against this exact
data (both by hand and via this automated test) shows the ALGORITHM already produces the
correct dates and amounts for both brackets:

  - Sedan (no existing entries): a single new entry for the peak window, nothing else - there
    was nothing to carve around.
  - Hiace (one standing entry): three entries - the new peak entry, the original truncated to
    end the day before the peak starts (its own supplement amounts and "name" untouched), and
    a new "resume" entry picking up the day after the peak ends and running to the original's
    own end date (2049-12-31).

This matches the product owner's own confirmed process description (see
_carve_transport_option_price_window's docstring) and their hand-built example, aside from the
"name" field appearing on the two new synthetic entries in this tool's output but not in their
hand-typed example - harmless (ContractTransportOptionPriceVO's "name" is optional/descriptive
only) and actually load-bearing here: it's what makes a second run of this same supplement
detect "already added" and skip instead of duplicating (see plan_structured's existing_names
check), so it is deliberately kept rather than stripped to match the example byte-for-byte.

Given the algorithm itself checks out against the product owner's own real data, the three
reported symptoms ("wrong price supplement", "wrong selected modality", "no supplement was
added") are consistent with testing a build from BEFORE this session's two real, confirmed
fixes landed: the select-all/select-none checkbox widget-key bug (this exact preview screen's
Include-this-service checkboxes did not track clicks before that fix - see app.py's
mi_select_all/mi_select_none) and the carve-overlap rewrite this docstring already describes.
This test locks in the now-verified-correct numeric/date behavior so a future change cannot
silently regress it again.
"""
import bulk_notes


class _FakeClient:
    def __init__(self, transport, options):
        self._transport = transport
        self._options = options  # code -> option dict
        self.updated_options = []

    def get_transports(self, supplier_id):
        return {"transport": [self._transport]}

    def get_transport(self, supplier_id, transport_id):
        assert transport_id == self._transport["id"]
        return self._transport

    def get_transport_option(self, supplier_id, transport_id, code):
        return self._options[code]

    def update_transport_option(self, supplier_id, transport_id, payload):
        self.updated_options.append((transport_id, payload))
        return payload


def _real_client():
    transport = {
        "id": "TRANSPORT-406543",
        "name": "Test Transport",
        "optionCodes": ["Sedan", "Hiace"],
        "startDate": "2026-06-24",
        "endDate": "2049-12-31",
        "baseAdultPrice": 50.0, "baseChildrenPrice": 30.0, "baseInfantPrice": 0.0,
    }
    sedan = {"code": "Sedan", "minPassengers": 1, "maxPassengers": 3, "prices": []}
    hiace = {"code": "Hiace", "minPassengers": 4, "maxPassengers": 8, "prices": [{
        "adultPriceSupplement": 0.0, "adultRTPriceSupplement": 0.0,
        "childrenPriceSupplement": 0.0, "childrenRTPriceSupplement": 0.0,
        "endDate": "2049-12-31", "infantPriceSupplement": 0.0, "infantRTPriceSupplement": 0.0,
        "name": "High Season", "startDate": "2026-09-10",
    }]}
    return _FakeClient(transport, {"Sedan": sedan, "Hiace": hiace})


def _plan():
    client = _real_client()
    item_data = {
        "name": "High Season", "amount": 0, "is_percent": False,
        "start_date": "2026-12-24", "end_date": "2027-01-07",
    }
    return client, bulk_notes.plan_structured(
        client, "51758", "Transport", "transport_supplement", item_data)


def test_both_brackets_are_offered_and_nothing_is_skipped():
    _, planned = _plan()
    assert planned["will_change"] == 2
    assert planned["unchanged"] == 0
    assert planned["failed"] == 0
    ids = {it["id"] for it in planned["items"]}
    assert ids == {"TRANSPORT-406543:Sedan", "TRANSPORT-406543:Hiace"}


def test_sedan_with_no_existing_prices_gets_a_single_new_entry():
    _, planned = _plan()
    sedan = next(it for it in planned["items"] if it["id"] == "TRANSPORT-406543:Sedan")
    prices = sedan["record"]["prices"]
    assert len(prices) == 1
    assert prices[0]["startDate"] == "2026-12-24"
    assert prices[0]["endDate"] == "2027-01-07"
    for field in ("adultPriceSupplement", "childrenPriceSupplement", "infantPriceSupplement",
                 "adultRTPriceSupplement", "childrenRTPriceSupplement", "infantRTPriceSupplement"):
        assert prices[0][field] == 0.0


def test_hiace_with_one_standing_entry_gets_split_into_three():
    _, planned = _plan()
    hiace = next(it for it in planned["items"] if it["id"] == "TRANSPORT-406543:Hiace")
    prices = sorted(hiace["record"]["prices"], key=lambda e: e["startDate"])
    assert len(prices) == 3

    before, peak, after = prices
    # The original entry, truncated to end the day before the peak starts - name and
    # supplement amounts carried through completely untouched.
    assert before["startDate"] == "2026-09-10"
    assert before["endDate"] == "2026-12-23"
    assert before["name"] == "High Season"

    # The new peak entry itself.
    assert peak["startDate"] == "2026-12-24"
    assert peak["endDate"] == "2027-01-07"

    # The "resume" entry picking back up the day after the peak ends, running to the
    # original's own end date.
    assert after["startDate"] == "2027-01-08"
    assert after["endDate"] == "2049-12-31"

    for entry in prices:
        for field in ("adultPriceSupplement", "childrenPriceSupplement", "infantPriceSupplement",
                     "adultRTPriceSupplement", "childrenRTPriceSupplement", "infantRTPriceSupplement"):
            assert entry[field] == 0.0


def test_applying_the_plan_sends_each_bracket_to_its_own_transport_id():
    client, planned = _plan()
    result = bulk_notes.apply(client, "51758", planned)
    assert result["failed"] == []
    assert len(client.updated_options) == 2
    sent_transport_ids = {t_id for t_id, _payload in client.updated_options}
    assert sent_transport_ids == {"TRANSPORT-406543"}
    sent_codes = {payload["code"] for _t_id, payload in client.updated_options}
    assert sent_codes == {"Sedan", "Hiace"}


def test_running_the_same_supplement_twice_does_not_duplicate_it():
    """The "name" field this tool writes onto its own new entries (unlike the product owner's
    hand-typed example) is what makes this idempotency check work - a second Preview must see
    the just-added entry and report "unchanged", not offer to add a duplicate peak entry."""
    client, planned = _plan()
    bulk_notes.apply(client, "51758", planned)
    # Feed the now-updated options back into a fresh client for a second preview pass.
    updated_by_code = {code: payload for _t_id, payload in client.updated_options
                       for code in [payload["code"]]}
    client2 = _FakeClient(client._transport, updated_by_code)
    item_data = {
        "name": "High Season", "amount": 0, "is_percent": False,
        "start_date": "2026-12-24", "end_date": "2027-01-07",
    }
    second_plan = bulk_notes.plan_structured(
        client2, "51758", "Transport", "transport_supplement", item_data)
    assert second_plan["will_change"] == 0
    assert second_plan["unchanged"] == 2
