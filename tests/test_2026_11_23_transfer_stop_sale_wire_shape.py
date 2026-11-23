"""Transfer stopSales wire shape: startDate/endDate, NOT start/end.

CONFIRMED REAL BUG (operator, 2026-11-23): bulk stop sales reported success on
Transfer records but TC's admin UI showed an empty stopSales list. Root cause
confirmed against docs/tc_api_reference.md line 10912:

    ContractTransferStopSaleVO:
        endDate: string
        startDate: string

The same field is called `stopSales` on every other contract (ClosedTour,
Ticket, Transport option, Hotel rate) BUT it points at LocalDateRangeVO which
has {start, end}. Transfer is the one outlier that uses the longer field
names. We were writing {start, end} - TC silently ignored those unknown fields
and the stopSales array stayed empty.

Pins the Transfer-specific serializer in flows/bulk_stop_sales.py so a future
refactor that normalises every stop-sale shape can't regress Transfer back
into silent-failure mode."""

from flows.bulk_stop_sales import (
    _normalise_transfer_stop_sale_in,
    _to_transfer_wire_shape,
)


def test_normalise_reads_startDate_endDate_from_live_record():
    """A live Transfer GET returns {startDate, endDate}; normalise to {start,end}
    so the generic merge code can compare it against new ranges."""
    entry = {"startDate": "2027-05-01", "endDate": "2027-10-31"}
    n = _normalise_transfer_stop_sale_in(entry)
    assert n == {"start": "2027-05-01", "end": "2027-10-31"}


def test_normalise_also_accepts_start_end_legacy_shape():
    """Defensive: an older record (or a hand-patched one) might still use start/end.
    Don't refuse to read it - normalise either shape to the canonical one."""
    entry = {"start": "2027-05-01", "end": "2027-10-31"}
    n = _normalise_transfer_stop_sale_in(entry)
    assert n == {"start": "2027-05-01", "end": "2027-10-31"}


def test_normalise_skips_entries_with_no_dates():
    """A malformed entry (missing both start/startDate and end/endDate) is dropped."""
    assert _normalise_transfer_stop_sale_in({}) is None
    assert _normalise_transfer_stop_sale_in({"foo": "bar"}) is None


def test_wire_shape_writes_startDate_endDate_not_start_end():
    """The exact bug: write the LONG field names TC's Transfer schema requires."""
    out = _to_transfer_wire_shape({"start": "2027-05-01", "end": "2027-10-31"})
    assert out == {"startDate": "2027-05-01", "endDate": "2027-10-31"}, (
        "Transfer stopSales MUST use startDate/endDate per TC API spec. "
        "Writing start/end makes TC silently drop the fields and the stop sale "
        "disappears.")


def test_roundtrip_live_to_wire_preserves_dates():
    """GET returns startDate/endDate → normalise → add our new range → write
    back → every date lands in TC in the {startDate, endDate} shape."""
    live = [
        {"startDate": "2027-01-01", "endDate": "2027-01-05"},
        {"startDate": "2027-07-14", "endDate": "2027-07-14"},
    ]
    normalised = [_normalise_transfer_stop_sale_in(s) for s in live]
    # Add one new range
    merged = normalised + [{"start": "2027-05-01", "end": "2027-10-31"}]
    wire = [_to_transfer_wire_shape(r) for r in merged]
    assert wire == [
        {"startDate": "2027-01-01", "endDate": "2027-01-05"},
        {"startDate": "2027-07-14", "endDate": "2027-07-14"},
        {"startDate": "2027-05-01", "endDate": "2027-10-31"},
    ]
