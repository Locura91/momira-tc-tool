"""
The bulk supplement create / delete-all write paths (2026-11-15).

WHY THESE EXIST: delete-all wipes every supplement from every product of a supplier. It
shipped as the destructive half of four bulk tools, and until the loops were pulled out
of the Streamlit render functions into supplement_apply.py there was no way to exercise
it without a live Travel Compositor account. A bulk delete is the last thing that should
be running untested.

The cases that matter for a destructive bulk action:
  - it only touches records that actually have supplements
  - it empties the array rather than dropping the record's other fields
  - one failure does not abort the rest, and every failure is reported
  - it never mutates the caller's in-memory copies, so a failed write cannot leave the
    screen showing a state the account does not have
"""
import pytest

import supplement_apply as sa


class _FakeClient:
    """Records every write. `fail_on` makes specific records raise, to prove one failure
    does not stop the run."""

    def __init__(self, fail_on=(), error_on=()):
        self.writes = []
        self.posts = []
        self.fail_on = set(fail_on)
        self.error_on = set(error_on)

    def _record(self, kind, ident, payload):
        if ident in self.fail_on:
            raise RuntimeError("boom")
        self.writes.append((kind, ident, payload))
        if ident in self.error_on:
            return {"error": "rejected", "message": "TC said no"}
        return {"ok": True}

    def update_transfer(self, sid, payload):
        return self._record("transfer", payload.get("id"), payload)

    def update_ticket_option(self, sid, ticket_code, payload):
        return self._record("ticket", f"{ticket_code}/{payload.get('code')}", payload)

    def update_closed_tour(self, sid, payload):
        return self._record("tour", payload.get("code"), payload)

    def update_hotel(self, sid, payload):
        return self._record("hotel", payload.get("providerCode"), payload)

    def add_hotel_supplement(self, sid, provider_code, supp):
        if provider_code in self.fail_on:
            raise RuntimeError("boom")
        self.posts.append((provider_code, supp))
        return {"ok": True}


def _supp(name="S1"):
    return {"name": name, "amount": 10}


# ── fixtures per product type ────────────────────────────────────────────────

def _transfers():
    return [
        {"id": "T1", "name": "A→B", "supplements": [_supp("night")], "basePrice": 50},
        {"id": "T2", "name": "B→A", "supplements": [], "basePrice": 60},
        {"id": "T3", "name": "C→D", "supplements": [_supp("x"), _supp("y")]},
    ]


def _tickets():
    return [
        {"ticket_code": "TK1", "option_code": "M1", "ticket_name": "Walk",
         "option_name": "AM", "raw": {"code": "M1", "supplements": [_supp()],
                                      "baseAdultPrice": 20}},
        {"ticket_code": "TK1", "option_code": "M2", "ticket_name": "Walk",
         "option_name": "PM", "raw": {"code": "M2", "supplements": []}},
    ]


def _tours():
    return [
        {"code": "CT1", "name": "Bali", "supplements": [_supp()], "nights": 5},
        {"code": "CT2", "name": "Java", "supplements": []},
    ]


def _hotels():
    return [
        {"providerCode": "H1", "name": "Nile", "supplements": [_supp()], "rates": [{"id": 1}]},
        {"providerCode": "H2", "name": "Cairo", "supplements": []},
    ]


# ── delete-all touches only what has supplements ─────────────────────────────

@pytest.mark.parametrize("ptype,items,expected_ids", [
    ("Transfer", _transfers(), ["T1", "T3"]),
    ("Ticket", _tickets(), ["TK1/M1"]),
    ("ClosedTour", _tours(), ["CT1"]),
    ("Hotel", _hotels(), ["H1"]),
])
def test_delete_all_only_writes_records_that_have_supplements(ptype, items, expected_ids):
    """A PUT that changes nothing is still a write against a live record."""
    client = _FakeClient()
    res = sa.apply_delete_all(client, "123", ptype, items)
    assert [ident for _, ident, _ in client.writes] == expected_ids
    assert res["ok"] == len(expected_ids)
    assert res["failed"] == 0


@pytest.mark.parametrize("ptype,items", [
    ("Transfer", _transfers()), ("Ticket", _tickets()),
    ("ClosedTour", _tours()), ("Hotel", _hotels()),
])
def test_delete_all_empties_the_array(ptype, items):
    client = _FakeClient()
    sa.apply_delete_all(client, "123", ptype, items)
    assert client.writes, "nothing was written"
    for _, _, payload in client.writes:
        assert payload["supplements"] == []


def test_delete_all_preserves_every_other_field():
    """The record is PUT whole, so dropping a field here would silently wipe live data."""
    client = _FakeClient()
    sa.apply_delete_all(client, "123", "Transfer", _transfers())
    payload = next(p for _, i, p in client.writes if i == "T1")
    assert payload["name"] == "A→B"
    assert payload["basePrice"] == 50


def test_delete_all_does_not_mutate_the_callers_copies():
    """If the write fails, the screen must not already show the supplements as gone."""
    items = _transfers()
    sa.apply_delete_all(_FakeClient(fail_on={"T1", "T3"}), "123", "Transfer", items)
    assert items[0]["supplements"] == [_supp("night")]
    assert len(items[2]["supplements"]) == 2


def test_delete_all_with_nothing_to_delete_writes_nothing():
    client = _FakeClient()
    res = sa.apply_delete_all(client, "123", "Transfer",
                              [{"id": "T9", "name": "none", "supplements": []}])
    assert client.writes == []
    assert res == {"ok": 0, "failed": 0, "errors": [], "total": 0}


# ── failures are survived and reported ───────────────────────────────────────

def test_one_failure_does_not_abort_the_rest():
    client = _FakeClient(fail_on={"T1"})
    res = sa.apply_delete_all(client, "123", "Transfer", _transfers())
    assert res["ok"] == 1 and res["failed"] == 1
    assert [i for _, i, _ in client.writes] == ["T3"]
    assert "T1" in res["errors"][0]


def test_an_error_dict_counts_as_a_failure_not_a_success():
    """Travel Compositor answers some rejections with 200 and an error body."""
    client = _FakeClient(error_on={"T1"})
    res = sa.apply_delete_all(client, "123", "Transfer", _transfers())
    assert res["ok"] == 1 and res["failed"] == 1
    assert "TC said no" in res["errors"][0]


def test_progress_is_reported_for_every_item():
    seen = []
    sa.apply_delete_all(_FakeClient(), "123", "Transfer", _transfers(),
                        progress=lambda n, total: seen.append((n, total)))
    assert seen == [(1, 2), (2, 2)]


# ── create keeps what is already there ───────────────────────────────────────

@pytest.mark.parametrize("ptype,items", [
    ("Transfer", _transfers()), ("Ticket", _tickets()), ("ClosedTour", _tours()),
])
def test_create_appends_rather_than_replacing(ptype, items):
    client = _FakeClient()
    sa.apply_create(client, "123", ptype, items, _supp("new"))
    for _, _, payload in client.writes:
        assert payload["supplements"][-1]["name"] == "new"
    # The record that already had two keeps both and gains one.
    if ptype == "Transfer":
        t3 = next(p for _, i, p in client.writes if i == "T3")
        assert [s["name"] for s in t3["supplements"]] == ["x", "y", "new"]


def test_create_writes_every_record_including_empty_ones():
    """Unlike delete, create legitimately targets records with no supplements yet."""
    client = _FakeClient()
    res = sa.apply_create(client, "123", "Transfer", _transfers(), _supp("new"))
    assert res["ok"] == 3


def test_create_respects_the_selection():
    client = _FakeClient()
    res = sa.apply_create(client, "123", "Transfer", _transfers(), _supp("new"),
                          selected_labels={"T2"})
    assert [i for _, i, _ in client.writes] == ["T2"]
    assert res["ok"] == 1


def test_create_does_not_share_one_supplement_object_between_records():
    """Each record must get its own copy, or editing one in TC later would appear to
    change the others."""
    client = _FakeClient()
    supp = _supp("shared")
    sa.apply_create(client, "123", "Transfer", _transfers(), supp)
    written = [p["supplements"][-1] for _, _, p in client.writes]
    assert all(w == supp for w in written)
    assert len({id(w) for w in written}) == len(written)
    assert all(w is not supp for w in written)


def test_create_does_not_mutate_the_callers_copies():
    items = _transfers()
    sa.apply_create(_FakeClient(), "123", "Transfer", items, _supp("new"))
    assert [s["name"] for s in items[0]["supplements"]] == ["night"]
    assert items[1]["supplements"] == []


def test_hotel_create_uses_the_post_endpoint_not_a_record_put():
    """Hotel supplements have their own creation endpoint; writing the record back
    instead would not create one."""
    client = _FakeClient()
    res = sa.apply_create(client, "123", "Hotel", _hotels(), _supp("new"))
    assert [code for code, _ in client.posts] == ["H1", "H2"]
    assert client.writes == []
    assert res["ok"] == 2


def test_hotel_delete_uses_the_record_put_since_no_delete_endpoint_exists():
    client = _FakeClient()
    sa.apply_delete_all(client, "123", "Hotel", _hotels())
    assert [i for _, i, _ in client.writes] == ["H1"]
    assert client.posts == []
