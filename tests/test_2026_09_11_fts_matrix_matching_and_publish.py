"""Tests for the FTS matrix bulk-import matching/publish helpers (fts_transfer_matrix.py, added
2026-09-11 per explicit product-owner instruction: "it must auto match, but the human must
verify it before update").

match_fts_candidates_to_existing is the AUTO part (pure, deterministic, scores every candidate
against the supplier's existing transports and tags create/update) - these tests lock in that it
never fetches per-route (one existing_transports list is reused for all candidates) and that a
strong name match is required before it ever suggests "update".

publish_fts_candidate is the per-route publish sequence (parent create/update, then one Option
per occupancy bracket, then stale-option deactivation) - these tests lock in the two-stage call
order, that force_base_occupancy is always passed through so Sedan stays the base, and that a
failure on one route is caught and reported rather than raised (so it can't take down a 271-route
batch).
"""
import os

from schemas import TransportHumanPreConfig
from fts_transfer_matrix import (
    match_fts_candidates_to_existing, publish_fts_candidate, FTS_SEDAN_BRACKET, FTS_HIACE_BRACKET,
)

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


def test_app_py_wires_up_the_fts_matrix_import_flow():
    src = _read_app_py()
    assert "def render_fts_matrix_import_flow(" in src
    assert "render_fts_matrix_import_flow(client, supplier_id, currency, release_days)" in src
    assert "build_fts_matrix_candidates" in src
    assert "match_fts_candidates_to_existing" in src
    assert "publish_fts_candidate" in src
    # The verify-before-update gate must actually be wired to the checkbox, not just present in
    # a comment somewhere - confirm the publish list only includes an "update" row when verified.
    assert 'creates + [c for c in updates if c["verified"]]' in src


def _candidate(departure="Cairo", arrival="Marsa Alam", sedan=215.0, hiace=270.0):
    return {
        "departure_name": departure, "arrival_name": arrival,
        "sedan_price": sedan, "hiace_price": hiace,
        "extracted_transport_data": {
            "departure_name": departure, "arrival_name": arrival,
            "service_name": "Private Transfer", "charge_unit": "per_service", "currency": "USD",
            "occupancy_brackets": [
                {"min_occupancy": 1, "max_occupancy": 3, "price": sedan},
                {"min_occupancy": 1, "max_occupancy": 8, "price": hiace},
            ],
        },
    }


# ======================================================================
# match_fts_candidates_to_existing
# ======================================================================
def test_no_existing_transports_everything_is_create():
    candidates = [_candidate("Cairo", "Luxor"), _candidate("Aswan", "Hurghada")]
    matched = match_fts_candidates_to_existing(candidates, [])
    assert all(m["action"] == "create" for m in matched)
    assert all(m["matched_transport_id"] is None for m in matched)


def test_strong_name_match_is_suggested_as_update():
    candidates = [_candidate("Cairo", "Marsa Alam")]
    existing = [{"id": "TRANSPORT-999", "name": "Cairo - Marsa Alam"}]
    matched = match_fts_candidates_to_existing(candidates, existing)
    assert matched[0]["action"] == "update"
    assert matched[0]["matched_transport_id"] == "TRANSPORT-999"
    assert matched[0]["matched_transport_name"] == "Cairo - Marsa Alam"
    assert matched[0]["match_score"] is not None


def test_unrelated_existing_transport_does_not_trigger_a_false_update():
    candidates = [_candidate("Cairo", "Marsa Alam")]
    existing = [{"id": "TRANSPORT-1", "name": "Praslin - La Digue"}]
    matched = match_fts_candidates_to_existing(candidates, existing)
    assert matched[0]["action"] == "create"
    assert matched[0]["matched_transport_id"] is None


def test_inputs_are_not_mutated():
    candidates = [_candidate("Cairo", "Luxor")]
    existing = [{"id": "T1", "name": "Cairo - Luxor"}]
    match_fts_candidates_to_existing(candidates, existing)
    assert "action" not in candidates[0]  # original list untouched, a new list was returned


def test_reuses_one_existing_transports_list_for_every_candidate_no_per_route_refetch():
    # existing_transports is passed in once (not fetched inside this function at all) - this
    # just confirms scoring 50 candidates against the same list works without issue/side effects,
    # since the real motivation (271 routes, one client.get_transports call) is an app.py-level
    # concern this function has no API surface to violate in the first place.
    candidates = [_candidate(f"City{i}", f"Dest{i}") for i in range(50)]
    existing = [{"id": "T1", "name": "Cairo - Marsa Alam"}]
    matched = match_fts_candidates_to_existing(candidates, existing)
    assert len(matched) == 50


# ======================================================================
# publish_fts_candidate
# ======================================================================
class _FakeClient:
    def __init__(self):
        self.calls = []
        self.transports = {}  # id -> dict
        self.options = {}     # (transport_id, code) -> dict
        self._next_id = 1000
        self.fail_create_transport = False
        self.fail_option_code = None

    def resolve_transport_base(self, query_term):
        return {"code": "X" + (query_term or "")[:3].upper(), "name": query_term,
                "valid": True, "match_type": "exact"}

    def get_transport(self, supplier_id, transport_id):
        self.calls.append(("get_transport", transport_id))
        return self.transports.get(transport_id, {"error": "not found"})

    def get_transport_option(self, supplier_id, transport_id, code):
        self.calls.append(("get_transport_option", transport_id, code))
        return self.options.get((transport_id, code), {"error": "not found"})

    def create_transport(self, supplier_id, payload):
        self.calls.append(("create_transport", payload.get("name")))
        if self.fail_create_transport:
            return {"error": "boom"}
        new_id = f"TRANSPORT-{self._next_id}"
        self._next_id += 1
        self.transports[new_id] = {**payload, "id": new_id}
        return {"id": new_id}

    def update_transport(self, supplier_id, payload):
        self.calls.append(("update_transport", payload.get("id")))
        return {"id": payload.get("id")}

    def create_transport_option(self, supplier_id, transport_id, payload):
        self.calls.append(("create_transport_option", transport_id, payload.get("code")))
        if payload.get("code") == self.fail_option_code:
            return {"error": "option boom"}
        return payload

    def update_transport_option(self, supplier_id, transport_id, payload):
        self.calls.append(("update_transport_option", transport_id, payload.get("code")))
        return payload


def test_publish_create_path_calls_create_then_options_in_order():
    client = _FakeClient()
    pre_config = TransportHumanPreConfig(supplier_id="55555", currency="USD")
    candidate = {**_candidate("Cairo", "Luxor"), "action": "create", "matched_transport_id": None}
    result = publish_fts_candidate(client, "55555", pre_config, candidate)
    assert result["ok"] is True
    assert result["errors"] == []
    assert result["transport_id"] is not None
    call_kinds = [c[0] for c in client.calls]
    assert call_kinds[0] == "create_transport"
    assert call_kinds.count("create_transport_option") >= 2  # Sedan + Hiace at minimum
    assert "update_transport" not in call_kinds
    assert "get_transport" not in call_kinds  # no existing id -> no merge fetch


def test_publish_update_path_fetches_existing_then_updates():
    client = _FakeClient()
    client.transports["TRANSPORT-42"] = {
        "id": "TRANSPORT-42", "optionCodes": ["OPT-SEDAN"], "startDate": "2026-01-01",
        "endDate": "2026-12-31",
    }
    client.options[("TRANSPORT-42", "OPT-SEDAN")] = {
        "code": "OPT-SEDAN", "minPassengers": 1, "maxPassengers": 3, "prices": [],
    }
    pre_config = TransportHumanPreConfig(supplier_id="55555", currency="USD")
    candidate = {**_candidate("Cairo", "Luxor"), "action": "update",
                 "matched_transport_id": "TRANSPORT-42"}
    result = publish_fts_candidate(client, "55555", pre_config, candidate)
    assert result["ok"] is True
    assert result["transport_id"] == "TRANSPORT-42"
    call_kinds = [c[0] for c in client.calls]
    assert "get_transport" in call_kinds
    assert "get_transport_option" in call_kinds
    assert "update_transport" in call_kinds
    assert "create_transport" not in call_kinds
    # Sedan bracket matched an existing option by occupancy -> updated, not duplicated.
    update_option_codes = [c[2] for c in client.calls if c[0] == "update_transport_option"]
    assert "OPT-SEDAN" in update_option_codes


def test_publish_forces_sedan_as_base_not_the_wider_hiace_bracket():
    client = _FakeClient()
    pre_config = TransportHumanPreConfig(supplier_id="55555", currency="USD")
    candidate = {**_candidate("Cairo", "Marsa Alam", sedan=215.0, hiace=270.0),
                 "action": "create", "matched_transport_id": None}
    publish_fts_candidate(client, "55555", pre_config, candidate)
    created = list(client.transports.values())[0]
    assert created["vehiclePrice"] == 215.0  # Sedan, not the wider Hiace bracket


def test_create_transport_failure_is_caught_and_reported_not_raised():
    client = _FakeClient()
    client.fail_create_transport = True
    pre_config = TransportHumanPreConfig(supplier_id="55555", currency="USD")
    candidate = {**_candidate("Cairo", "Luxor"), "action": "create", "matched_transport_id": None}
    result = publish_fts_candidate(client, "55555", pre_config, candidate)
    assert result["ok"] is False
    assert result["transport_id"] is None
    assert result["errors"]


def test_option_failure_is_reported_but_transport_id_still_returned():
    client = _FakeClient()
    client.fail_option_code = None  # set after we know the generated code below
    pre_config = TransportHumanPreConfig(supplier_id="55555", currency="USD")
    candidate = {**_candidate("Cairo", "Luxor"), "action": "create", "matched_transport_id": None}
    # Build once to discover a real option code, then re-run with that code forced to fail -
    # simpler and more honest than guessing the generated code format here.
    from builder import build_transport_payloads
    built = build_transport_payloads(pre_config, candidate["extracted_transport_data"], client,
                                     force_base_occupancy=FTS_SEDAN_BRACKET)
    a_code = built["option_actions"][0]["code"]
    client.fail_option_code = a_code
    result = publish_fts_candidate(client, "55555", pre_config, candidate)
    assert result["ok"] is False
    assert result["transport_id"] is not None  # parent still published
    assert any(a_code in e for e in result["errors"])


def test_client_exception_during_get_transport_is_tolerated_not_fatal():
    class _ExplodingClient(_FakeClient):
        def get_transport(self, supplier_id, transport_id):
            raise RuntimeError("network blip")

    client = _ExplodingClient()
    pre_config = TransportHumanPreConfig(supplier_id="55555", currency="USD")
    candidate = {**_candidate("Cairo", "Luxor"), "action": "update",
                 "matched_transport_id": "TRANSPORT-99"}
    result = publish_fts_candidate(client, "55555", pre_config, candidate)
    # Merge fetch failed, but the update still goes through (without merge-preserved fields),
    # same tolerant behavior as the single-route flow.
    assert result["ok"] is True
    assert result["transport_id"] == "TRANSPORT-99"
