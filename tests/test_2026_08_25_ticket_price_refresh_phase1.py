"""Regression tests for Phase 1 of the 2026-08-25 Ticket price-refresh feature (product owner:
"the next developement must be done, when we are talking about updating Tickets or ClosedTours
for the new Seasons with new prices... Could we plan this the same for Tickets and closedtours...
Easiest part would be starting with Ticket." then, after 3 clarifying design decisions (always-
add-never-replace for Peak Season supplements; 15% of base adult/child price for percentage
surcharges; Ticket-only scope now but also refresh existing language-choice supplement prices) -
"yes, please start with phase 1").

Phase 1 is base/occupancy price ONLY: load existing Ticket Modalities from Travel Compositor,
match by CODE, diff against the document, human review, apply. Peak Season supplement creation
(Phase 2) and language-choice supplement price refresh (Phase 3) are explicitly NOT covered by
any of these tests, matching the deferred scope.
"""
import json

import price_refresh
from price_refresh import (
    KIND_TICKET,
    _ticket_occupancy_options,
    _ticket_price_type_supported,
    load_supplier_tickets,
    lookup_ticket_prices,
    build_ticket_proposals,
    rebuild_ticket_prices,
    apply_ticket_proposals,
)


# ---------------------------------------------------------------------------
# _ticket_occupancy_options / _ticket_price_type_supported
# ---------------------------------------------------------------------------

def test_occupancy_rows_split_into_adult_and_child_by_age_range_key():
    modality = {"occupancyPrices": [
        {"occupancy": 1, "amount": 45.0},
        {"occupancy": 1, "amount": 30.0, "ageRange": {"min": 2, "max": 11}},
        {"occupancy": 2, "amount": 40.0},
    ]}
    adult, child = _ticket_occupancy_options(modality)
    assert [o["code"] for o in adult] == ["occ1", "occ2"]
    assert adult[0]["unit_price"] == 45.0
    assert [o["code"] for o in child] == ["occ1"]
    assert child[0]["unit_price"] == 30.0


def test_occupancy_rows_with_zero_or_missing_occupancy_are_skipped():
    modality = {"occupancyPrices": [{"occupancy": 0, "amount": 10.0}, "not a dict", {"amount": 5.0}]}
    adult, child = _ticket_occupancy_options(modality)
    assert adult == [] and child == []


def test_price_type_supported_only_for_occupancy():
    assert _ticket_price_type_supported("OCCUPANCY") is True
    assert _ticket_price_type_supported(None) is True  # default assumed OCCUPANCY
    assert _ticket_price_type_supported("DISTRIBUTION") is False
    assert _ticket_price_type_supported("SERVICE") is False


# ---------------------------------------------------------------------------
# load_supplier_tickets
# ---------------------------------------------------------------------------

class _FakeTicketClient:
    def __init__(self, tickets, options, get_ticket_option_errors=None):
        self._tickets = tickets
        self._options = options  # {(ticket_code, modality_code): option_dict}
        self._errors = get_ticket_option_errors or set()

    def get_tickets(self, supplier_id, first=0, limit=50):
        page = self._tickets[first:first + limit]
        return {"tickets": page, "pagination": {"totalResults": len(self._tickets)}}

    def get_ticket_option(self, supplier_id, ticket_code, option_code):
        if (ticket_code, option_code) in self._errors:
            return {"error": 500, "message": "boom"}
        return self._options[(ticket_code, option_code)]


def _modality(code, price_type="OCCUPANCY", occupancy_prices=None, extra=None):
    m = {"code": code, "priceType": price_type,
         "occupancyPrices": occupancy_prices or [{"occupancy": 1, "amount": 45.0}]}
    if extra:
        m.update(extra)
    return m


def test_load_supplier_tickets_builds_one_route_per_ticket_modality_pair():
    tickets = [{"code": "ALX-01", "name": "Alexandria Tour", "currency": "USD",
               "modalityCodes": ["MOD-A", "MOD-B"]}]
    options = {
        ("ALX-01", "MOD-A"): _modality("MOD-A"),
        ("ALX-01", "MOD-B"): _modality("MOD-B", occupancy_prices=[{"occupancy": 2, "amount": 80.0}]),
    }
    client = _FakeTicketClient(tickets, options)
    routes, err = load_supplier_tickets(client, "123")
    assert err is None
    assert len(routes) == 2
    assert {r["modality_code"] for r in routes} == {"MOD-A", "MOD-B"}
    for r in routes:
        assert r["kind"] == KIND_TICKET
        assert r["ticket_code"] == "ALX-01"
        assert r["currency"] == "USD"
        assert r["price_type"] == "OCCUPANCY"
        assert r["fetch_failed"] is False


def test_load_supplier_tickets_flags_a_modality_that_could_not_be_read():
    tickets = [{"code": "ALX-01", "name": "Alexandria Tour", "currency": "USD",
               "modalityCodes": ["MOD-A"]}]
    client = _FakeTicketClient(tickets, {}, get_ticket_option_errors={("ALX-01", "MOD-A")})
    routes, err = load_supplier_tickets(client, "123")
    assert err is None
    assert len(routes) == 1
    assert routes[0]["fetch_failed"] is True
    assert routes[0]["options"] == []


def test_load_supplier_tickets_paginates_past_the_first_page():
    # 250 tickets: more than one 200-item page (the page size load_supplier_tickets requests),
    # so the second GET (first=200) must actually happen and its results must be included.
    n = 250
    tickets = [{"code": f"T-{i}", "name": f"Tour {i}", "currency": "EUR", "modalityCodes": ["MOD-A"]}
              for i in range(n)]
    options = {(f"T-{i}", "MOD-A"): _modality("MOD-A") for i in range(n)}
    client = _FakeTicketClient(tickets, options)  # honors first/limit as real offset/limit
    routes, err = load_supplier_tickets(client, "123")
    assert err is None
    assert {r["ticket_code"] for r in routes} == {f"T-{i}" for i in range(n)}


def test_load_supplier_tickets_reports_api_error():
    class _ErrClient:
        def get_tickets(self, supplier_id, first=0, limit=50):
            return {"error": 401, "message": "unauthorized"}
    routes, err = load_supplier_tickets(_ErrClient(), "123")
    assert routes == []
    assert "unauthorized" in err


# ---------------------------------------------------------------------------
# lookup_ticket_prices - dedupes by ticket_code before calling the AI
# ---------------------------------------------------------------------------

def _route(ticket_code, modality_code, options, currency="USD", price_type="OCCUPANCY",
          fetch_failed=False, raw=None, child_options=None):
    return {"kind": KIND_TICKET, "id": f"{ticket_code}/{modality_code}", "ticket_code": ticket_code,
            "modality_code": modality_code, "name": f"{ticket_code} — {modality_code}",
            "currency": currency, "price_type": price_type, "fetch_failed": fetch_failed,
            "options": options, "child_options": child_options or [],
            "raw": raw if raw is not None else {"code": modality_code, "priceType": price_type,
                                                "occupancyPrices": [
                                                    {"occupancy": o["min_pax"], "amount": o["unit_price"]}
                                                    for o in options]}}


def test_lookup_ticket_prices_calls_the_ai_once_per_unique_code(monkeypatch):
    calls = []

    def fake_call(system_prompt, user_content, model, max_tokens=8192, input_schema=None):
        calls.append(user_content)
        # Two lines under "TICKETS TO PRICE:" -> confirms dedup happened (only ONE index: 0).
        assert user_content.count("code: ALX-01") == 1
        return {"routes": [{"index": 0, "found": True, "matched_row": "ALX-01 row",
                            "currency": "USD", "minimum_pax": 1,
                            "brackets": [{"min_pax": 1, "max_pax": 1, "price": 50.0,
                                         "child_price": None, "infant_price": None}]}]}

    monkeypatch.setattr(price_refresh.ai_extractor, "_call_claude", fake_call)
    opt_a = [{"code": "occ1", "min_pax": 1, "max_pax": 1, "unit_price": 45.0, "name": "1 pax"}]
    opt_b = [{"code": "occ1", "min_pax": 1, "max_pax": 1, "unit_price": 45.0, "name": "1 pax"}]
    routes = [_route("ALX-01", "MOD-A", opt_a), _route("ALX-01", "MOD-B", opt_b)]
    findings = lookup_ticket_prices(routes, "some document text")
    assert len(calls) == 1
    # Both routes (same ticket_code) get the SAME finding, fanned out from one AI call.
    assert findings[0]["brackets"][0]["price"] == 50.0
    assert findings[1]["brackets"][0]["price"] == 50.0


def test_lookup_ticket_prices_returns_empty_without_a_document_or_routes():
    assert lookup_ticket_prices([], "some text") == {}
    assert lookup_ticket_prices([_route("ALX-01", "MOD-A", [])], "") == {}


# ---------------------------------------------------------------------------
# build_ticket_proposals
# ---------------------------------------------------------------------------

def _finding(brackets, found=True, minimum_pax=1, currency="", matched_row="", confidence="high", note=""):
    return {"found": found, "brackets": brackets, "minimum_pax": minimum_pax, "currency": currency,
            "matched_row": matched_row, "confidence": confidence, "note": note}


def test_changed_status_when_the_document_prices_it_differently():
    opts = [{"code": "occ1", "min_pax": 1, "max_pax": 1, "unit_price": 45.0, "name": "1 pax"}]
    route = _route("ALX-01", "MOD-A", opts)
    finding = _finding([{"min_pax": 1, "max_pax": 1, "price": 50.0, "child_price": None, "infant_price": None}])
    proposals = build_ticket_proposals([route], {0: finding})
    assert proposals[0]["status"] == "changed"
    assert proposals[0]["accepted"] is True
    assert proposals[0]["changes"][0]["old"] == 45.0
    assert proposals[0]["changes"][0]["new"] == 50.0


def test_unchanged_status_when_the_document_matches_the_live_price():
    opts = [{"code": "occ1", "min_pax": 1, "max_pax": 1, "unit_price": 45.0, "name": "1 pax"}]
    route = _route("ALX-01", "MOD-A", opts)
    finding = _finding([{"min_pax": 1, "max_pax": 1, "price": 45.0, "child_price": None, "infant_price": None}])
    proposals = build_ticket_proposals([route], {0: finding})
    assert proposals[0]["status"] == "unchanged"
    assert proposals[0]["accepted"] is False


def test_not_in_document_status_when_the_code_was_not_found():
    opts = [{"code": "occ1", "min_pax": 1, "max_pax": 1, "unit_price": 45.0, "name": "1 pax"}]
    route = _route("ALX-01", "MOD-A", opts)
    finding = _finding([], found=False)
    proposals = build_ticket_proposals([route], {0: finding})
    assert proposals[0]["status"] == "not_in_document"


def test_blocked_unreadable_status_for_a_modality_that_failed_to_fetch():
    route = _route("ALX-01", "MOD-A", [], fetch_failed=True)
    proposals = build_ticket_proposals([route], {})
    assert proposals[0]["status"] == "blocked_unreadable"
    assert proposals[0]["accepted"] is False
    assert proposals[0]["unreadable_options"] == ["MOD-A"]


def test_unsupported_price_type_status_for_distribution_modality():
    route = _route("ALX-01", "MOD-A", [], price_type="DISTRIBUTION")
    proposals = build_ticket_proposals([route], {})
    assert proposals[0]["status"] == "unsupported_price_type"
    assert proposals[0]["accepted"] is False


def test_child_price_from_document_is_reported_on_the_change():
    opts = [{"code": "occ2", "min_pax": 2, "max_pax": 2, "unit_price": 40.0, "name": "2 pax"}]
    child_opts = [{"code": "occ2", "min_pax": 2, "max_pax": 2, "unit_price": 25.0, "name": "2 pax (child)"}]
    route = _route("ALX-01", "MOD-A", opts, child_options=child_opts)
    finding = _finding([{"min_pax": 2, "max_pax": 2, "price": 45.0, "child_price": 30.0, "infant_price": None}])
    proposals = build_ticket_proposals([route], {0: finding})
    change = proposals[0]["changes"][0]
    assert change["new"] == 45.0
    assert change["child_old"] == 25.0
    assert change["child_new"] == 30.0


def test_child_price_absent_from_document_leaves_child_new_none():
    opts = [{"code": "occ2", "min_pax": 2, "max_pax": 2, "unit_price": 40.0, "name": "2 pax"}]
    child_opts = [{"code": "occ2", "min_pax": 2, "max_pax": 2, "unit_price": 25.0, "name": "2 pax (child)"}]
    route = _route("ALX-01", "MOD-A", opts, child_options=child_opts)
    finding = _finding([{"min_pax": 2, "max_pax": 2, "price": 45.0, "child_price": None, "infant_price": None}])
    proposals = build_ticket_proposals([route], {0: finding})
    change = proposals[0]["changes"][0]
    assert change["child_old"] == 25.0
    assert change["child_new"] is None  # never guessed - only the adult price is directly reported


# ---------------------------------------------------------------------------
# rebuild_ticket_prices - only occupancyPrices change, everything else survives
# ---------------------------------------------------------------------------

def test_rebuild_rewrites_the_matching_adult_row_and_leaves_other_fields_untouched():
    raw = {
        "code": "MOD-A", "priceType": "OCCUPANCY", "startDate": "2020-01-01", "endDate": "2099-12-31",
        "remarks": {"EN": {"remarks": "Some notes"}},
        "occupancyPrices": [{"occupancy": 1, "amount": 45.0}, {"occupancy": 2, "amount": 80.0}],
    }
    route = {"raw": raw, "options": [
        {"code": "occ1", "min_pax": 1, "max_pax": 1, "unit_price": 45.0},
        {"code": "occ2", "min_pax": 2, "max_pax": 2, "unit_price": 80.0},
    ], "child_options": []}
    changes = [{"code": "occ1", "min_pax": 1, "max_pax": 1, "old": 45.0, "new": 50.0,
               "child_old": None, "child_new": None}]
    payload = rebuild_ticket_prices(route, changes)
    rows = {r["occupancy"]: r["amount"] for r in payload["occupancyPrices"]}
    assert rows[1] == 50.0
    assert rows[2] == 80.0  # untouched - no change proposed for this bracket
    assert payload["startDate"] == "2020-01-01"
    assert payload["endDate"] == "2099-12-31"
    assert payload["remarks"] == {"EN": {"remarks": "Some notes"}}
    # The source route's raw dict must never be mutated in place.
    assert raw["occupancyPrices"][0]["amount"] == 45.0


def test_rebuild_moves_child_price_by_the_same_ratio_when_document_gave_none():
    raw = {"code": "MOD-A", "priceType": "OCCUPANCY", "occupancyPrices": [
        {"occupancy": 2, "amount": 40.0},
        {"occupancy": 2, "amount": 20.0, "ageRange": {"min": 2, "max": 11}},
    ]}
    route = {"raw": raw, "options": [{"code": "occ2", "min_pax": 2, "max_pax": 2, "unit_price": 40.0}],
            "child_options": [{"code": "occ2", "min_pax": 2, "max_pax": 2, "unit_price": 20.0}]}
    changes = [{"code": "occ2", "min_pax": 2, "max_pax": 2, "old": 40.0, "new": 60.0,
               "child_old": 20.0, "child_new": None}]
    payload = rebuild_ticket_prices(route, changes)
    child_row = next(r for r in payload["occupancyPrices"] if "ageRange" in r)
    # ratio = 60/40 = 1.5 -> 20 * 1.5 = 30
    assert child_row["amount"] == 30.0


def test_rebuild_uses_explicit_child_price_from_the_document_when_given():
    raw = {"code": "MOD-A", "priceType": "OCCUPANCY", "occupancyPrices": [
        {"occupancy": 2, "amount": 40.0},
        {"occupancy": 2, "amount": 20.0, "ageRange": {"min": 2, "max": 11}},
    ]}
    route = {"raw": raw, "options": [{"code": "occ2", "min_pax": 2, "max_pax": 2, "unit_price": 40.0}],
            "child_options": [{"code": "occ2", "min_pax": 2, "max_pax": 2, "unit_price": 20.0}]}
    changes = [{"code": "occ2", "min_pax": 2, "max_pax": 2, "old": 40.0, "new": 60.0,
               "child_old": 20.0, "child_new": 35.0}]
    payload = rebuild_ticket_prices(route, changes)
    child_row = next(r for r in payload["occupancyPrices"] if "ageRange" in r)
    assert child_row["amount"] == 35.0  # taken directly, not ratio-derived


# ---------------------------------------------------------------------------
# apply_ticket_proposals
# ---------------------------------------------------------------------------

class _RecordingApplyClient:
    def __init__(self, fail_codes=None):
        self.calls = []
        self.fail_codes = fail_codes or set()

    def update_ticket_option(self, supplier_id, ticket_code, payload):
        self.calls.append((supplier_id, ticket_code, payload))
        if payload.get("code") in self.fail_codes:
            return {"error": 500, "message": "server exploded"}
        return {"code": payload.get("code")}


def _accepted_proposal(ticket_code="ALX-01", modality_code="MOD-A", old=45.0, new=50.0):
    raw = {"code": modality_code, "priceType": "OCCUPANCY",
          "occupancyPrices": [{"occupancy": 1, "amount": old}]}
    route = _route(ticket_code, modality_code, [
        {"code": "occ1", "min_pax": 1, "max_pax": 1, "unit_price": old, "name": "1 pax"}], raw=raw)
    return {"index": 0, "route": route, "accepted": True,
           "changes": [{"code": "occ1", "min_pax": 1, "max_pax": 1, "old": old, "new": new,
                       "child_old": None, "child_new": None}]}


def test_apply_updates_the_accepted_proposal_via_update_ticket_option():
    client = _RecordingApplyClient()
    proposal = _accepted_proposal()
    result = apply_ticket_proposals(client, "999", [proposal])
    assert len(result["updated"]) == 1
    assert result["failed"] == []
    supplier_id, ticket_code, payload = client.calls[0]
    assert supplier_id == "999"
    assert ticket_code == "ALX-01"
    assert payload["code"] == "MOD-A"
    assert payload["occupancyPrices"][0]["amount"] == 50.0


def test_apply_skips_unaccepted_and_changeless_proposals():
    client = _RecordingApplyClient()
    not_accepted = _accepted_proposal()
    not_accepted["accepted"] = False
    no_changes = _accepted_proposal()
    no_changes["changes"] = []
    result = apply_ticket_proposals(client, "999", [not_accepted, no_changes])
    assert result["updated"] == []
    assert result["skipped"] == 2
    assert client.calls == []


def test_apply_reports_a_failed_update_without_raising():
    client = _RecordingApplyClient(fail_codes={"MOD-A"})
    proposal = _accepted_proposal()
    result = apply_ticket_proposals(client, "999", [proposal])
    assert result["updated"] == []
    assert len(result["failed"]) == 1
    assert "server exploded" in result["failed"][0]["detail"]


def test_apply_reports_a_raised_exception_with_a_friendly_message():
    class _RaisingClient:
        def update_ticket_option(self, *a, **k):
            raise RuntimeError("network down")
    proposal = _accepted_proposal()
    result = apply_ticket_proposals(_RaisingClient(), "999", [proposal])
    assert result["updated"] == []
    assert len(result["failed"]) == 1
