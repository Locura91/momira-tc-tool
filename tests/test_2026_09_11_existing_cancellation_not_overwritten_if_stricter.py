"""Regression tests for the 2026-09-11 product-owner rule: "the app shall not overwrite any
cancellation, if there is an existing cancellation included, which is more strict: example:
100 percent refund is cancelled 60 days or prior --> no cancellation [change]. BUT: if existing
cancellation states 100% refund if cancelled 5 days or prior --> then we have to change it
within Cancellation and also if mentioned in the voucher remarks. On default for Momira Travel
is 100% refund if cancelled 30 days or prior."

Covers three layers: (1) the shared comparison in builder.py, (2) cancellation_bulk_transport.py
(Transport), (3) cancellation_bulk.py (ClosedTour/Ticket structured, Transfer/Hotel can't be
checked at all - no structured field to compare)."""
import builder
import cancellation_bulk as cb
import cancellation_bulk_transport as cbt


# ----------------------------------------------------------------------
# builder.cancellation_refund_at / existing_cancellation_at_least_as_strict
# ----------------------------------------------------------------------

def test_refund_at_picks_the_largest_applicable_days_threshold():
    ranges = [(60, 100.0), (14, 50.0)]
    assert builder.cancellation_refund_at(ranges, 90) == 100.0
    assert builder.cancellation_refund_at(ranges, 60) == 100.0
    assert builder.cancellation_refund_at(ranges, 30) == 50.0
    assert builder.cancellation_refund_at(ranges, 14) == 50.0


def test_refund_at_is_zero_when_no_tier_applies_or_ranges_empty():
    assert builder.cancellation_refund_at([(30, 100.0)], 10) == 0.0
    assert builder.cancellation_refund_at([], 100) == 0.0
    assert builder.cancellation_refund_at(None, 100) == 0.0


def test_existing_60_day_full_refund_is_stricter_than_30_day_default_worked_example():
    # THE PRODUCT OWNER'S OWN EXAMPLE: existing "100% refund at 60 days" must be left alone
    # against the 30-day house default.
    current = [(60, 100.0)]
    new_default = [(30, 100.0)]
    assert builder.existing_cancellation_at_least_as_strict(current, new_default) is True


def test_existing_5_day_full_refund_is_less_strict_than_30_day_default_worked_example():
    # THE PRODUCT OWNER'S OWN SECOND EXAMPLE: existing "100% refund at 5 days" must be changed.
    current = [(5, 100.0)]
    new_default = [(30, 100.0)]
    assert builder.existing_cancellation_at_least_as_strict(current, new_default) is False


def test_identical_policy_counts_as_at_least_as_strict():
    same = [(30, 100.0)]
    assert builder.existing_cancellation_at_least_as_strict(same, list(same)) is True


def test_empty_current_ranges_is_never_treated_as_already_strict():
    # Even though Travel Compositor's own UI treats an empty policy as 100% non-refundable
    # ("If empty, contract will be considered 100% non-refundable"), an unconfigured record
    # must still get the new policy applied - see the function's own docstring.
    assert builder.existing_cancellation_at_least_as_strict([], [(30, 100.0)]) is False
    assert builder.existing_cancellation_at_least_as_strict(None, [(30, 100.0)]) is False


def test_mixed_tiers_any_violation_anywhere_means_not_strict_enough():
    # Existing is stricter at 60+ days (100% refund only that far out) but MORE generous than
    # the default in the 14-30 day window (50% refund where the default gives 0%) - a single
    # violation anywhere means the whole existing policy must be brought back in line.
    current = [(60, 100.0), (14, 50.0)]
    new_default = [(30, 100.0)]
    assert builder.existing_cancellation_at_least_as_strict(current, new_default) is False


def test_current_strictly_stricter_at_every_breakpoint_is_kept():
    # 90-day full refund and 45-day 50% refund both require MORE notice than the 30-day/100%
    # default demands for the same or better refund at every point.
    current = [(90, 100.0), (45, 50.0)]
    new_default = [(30, 100.0)]
    assert builder.existing_cancellation_at_least_as_strict(current, new_default) is True


# ----------------------------------------------------------------------
# cancellation_bulk_transport.py (Transport - always has a structured field)
# ----------------------------------------------------------------------

class _FakeTransportClient:
    def __init__(self, transports):
        self._transports = transports
        self.update_calls = []

    def get_transports(self, supplier_id):
        return {"transport": self._transports}

    def get_transport(self, supplier_id, transport_id):
        return next((t for t in self._transports if t.get("id") == transport_id), {"error": 404})

    def update_transport(self, supplier_id, payload):
        self.update_calls.append((supplier_id, payload))
        return {"id": payload.get("id"), "code": 200}


def _transport_record(id_="T1", days=30, refund_pct=100.0,
                      description="<p>Private transfer.</p><p>Free cancellation up to 30 days before arrival.</p>"):
    return {
        "id": id_, "name": "Airport - Hotel",
        "segments": [{"departureLocationCode": "SSH", "arrivalLocationCode": "HTL"}],
        "cancellationRanges": [{"days": days, "percentage": refund_pct, "isBeforeStart": True}],
        "datasheets": {"EN": {"name": "Airport - Hotel", "description": description}},
    }


def test_transport_build_proposals_flags_existing_stricter_and_not_unchanged():
    client = _FakeTransportClient([_transport_record(days=60, refund_pct=100.0)])
    rows, _ = cbt.load_supplier_transports_for_cancellation(client, "SUP-X")
    proposals = cbt.build_proposals(rows, [{"days": 30, "fee_percentage": 0.0}])
    assert proposals[0]["existing_stricter"] is True
    assert proposals[0]["unchanged"] is False


def test_transport_build_proposals_does_not_flag_a_less_strict_existing_policy():
    client = _FakeTransportClient([_transport_record(days=5, refund_pct=100.0)])
    rows, _ = cbt.load_supplier_transports_for_cancellation(client, "SUP-X")
    proposals = cbt.build_proposals(rows, [{"days": 30, "fee_percentage": 0.0}])
    assert proposals[0]["existing_stricter"] is False


def test_transport_build_proposals_treats_empty_cancellation_ranges_as_needing_the_default():
    record = _transport_record()
    record["cancellationRanges"] = []
    client = _FakeTransportClient([record])
    rows, _ = cbt.load_supplier_transports_for_cancellation(client, "SUP-X")
    proposals = cbt.build_proposals(rows, [{"days": 30, "fee_percentage": 0.0}])
    assert proposals[0]["existing_stricter"] is False


def test_transport_apply_skips_existing_stricter_rows_without_writing():
    client = _FakeTransportClient([_transport_record(days=60, refund_pct=100.0)])
    rows, _ = cbt.load_supplier_transports_for_cancellation(client, "SUP-X")
    proposals = cbt.build_proposals(rows, [{"days": 30, "fee_percentage": 0.0}])
    results = cbt.apply_proposals(client, "SUP-X", proposals)
    assert results[0]["ok"] is True
    assert results[0]["skipped"] is True
    assert client.update_calls == []


def test_transport_apply_still_writes_a_row_that_needs_to_change():
    client = _FakeTransportClient([_transport_record(days=5, refund_pct=100.0)])
    rows, _ = cbt.load_supplier_transports_for_cancellation(client, "SUP-X")
    proposals = cbt.build_proposals(rows, [{"days": 30, "fee_percentage": 0.0}])
    results = cbt.apply_proposals(client, "SUP-X", proposals)
    assert results[0]["ok"] is True
    assert not results[0].get("skipped")
    assert len(client.update_calls) == 1


# ----------------------------------------------------------------------
# cancellation_bulk.py (ClosedTour has a structured field, Transfer does not)
# ----------------------------------------------------------------------

def _ct_record(code="CT1", days=30, refund_pct=100.0):
    return {
        "code": code,
        "datasheets": {"EN": {"name": "Test Tour",
                              "voucherRemarks": "Book now.\n\nFree cancellation up to 30 days before arrival."}},
        "cancellationRanges": [{"days": days, "percentage": refund_pct, "isBeforeStart": True}],
    }


class _FakeClosedTourClient:
    def __init__(self, records):
        self._records = records

    def get_closed_tour(self, supplier_id, code):
        return next((r for r in self._records if r.get("code") == code), {"error": 404})

    def update_closed_tour(self, supplier_id, payload):
        return {"code": payload.get("code"), "status": 200}


def test_closedtour_build_proposals_flags_existing_stricter():
    client = _FakeClosedTourClient([_ct_record(days=60, refund_pct=100.0)])
    rows, _ = cb.load_supplier_services_for_cancellation(client, "SUP-X", "ClosedTour", codes=["CT1"])
    proposals = cb.build_proposals(rows, [{"days": 30, "fee_percentage": 0.0}], "ClosedTour")
    assert proposals[0]["existing_stricter"] is True


def test_closedtour_build_proposals_does_not_flag_a_less_strict_existing_policy():
    client = _FakeClosedTourClient([_ct_record(days=5, refund_pct=100.0)])
    rows, _ = cb.load_supplier_services_for_cancellation(client, "SUP-X", "ClosedTour", codes=["CT1"])
    proposals = cb.build_proposals(rows, [{"days": 30, "fee_percentage": 0.0}], "ClosedTour")
    assert proposals[0]["existing_stricter"] is False


def test_closedtour_apply_skips_existing_stricter_rows_without_writing():
    client = _FakeClosedTourClient([_ct_record(days=60, refund_pct=100.0)])
    rows, _ = cb.load_supplier_services_for_cancellation(client, "SUP-X", "ClosedTour", codes=["CT1"])
    proposals = cb.build_proposals(rows, [{"days": 30, "fee_percentage": 0.0}], "ClosedTour")
    results = cb.apply_proposals(client, "SUP-X", "ClosedTour", proposals)
    assert results[0]["ok"] is True
    assert results[0]["skipped"] is True


class _FakeTransferClient:
    def __init__(self, records):
        self._records = records
        self.update_calls = []

    def get_transfers(self, supplier_id):
        return {"transfer": self._records}

    def update_transfer(self, supplier_id, payload):
        self.update_calls.append((supplier_id, payload))
        return {"id": payload.get("id"), "code": 200}


def test_transfer_existing_stricter_is_always_false_no_structured_field_to_compare():
    # Transfer has no cancellationRanges at all (cancellation_bulk.py's own docstring) - even
    # though the voucher text below plainly states a stricter policy in prose, this module
    # can't parse free text into (days, refund%) tiers, so it must NOT claim to know the
    # existing policy is stricter - it always allows the text to be rewritten, same as before
    # this rule existed.
    record = {
        "id": "TR1",
        "datasheets": {"EN": {"name": "Test Transfer",
                              "voucherRemarks": "Book now.\n\nFree cancellation up to 90 days before arrival."}},
    }
    client = _FakeTransferClient([record])
    rows, _ = cb.load_supplier_services_for_cancellation(client, "SUP-X", "Transfer")
    proposals = cb.build_proposals(rows, [{"days": 30, "fee_percentage": 0.0}], "Transfer")
    assert proposals[0]["existing_stricter"] is False
