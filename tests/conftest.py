"""
Shared pytest fixtures for the Momira platform test suite.

WHY A FAKE API CLIENT INSTEAD OF unittest.mock.Mock() EVERYWHERE: builder.py's payload
functions call several TravelCompositorAPI methods (resolve_destination,
resolve_transfer_zone_geolocation, ...) that each return a specific dict SHAPE the builder
code destructures (result["valid"], result["tc_code"], ...). A bare Mock() returns another
Mock for any attribute/call, which happily satisfies `.get(...)` calls but produces garbage
values with no useful assertion story and no protection if the real shape ever changes. This
fixture returns a small, real, hand-built object whose behaviour matches api_client.py's
documented contract - see resolve_destination's docstring in api_client.py for the shape this
mirrors.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Never touch the real developer's local platform_state.db from a test run, and never require
# a real DATABASE_URL - tests must be able to run offline, with no credentials, anywhere.
os.environ["PLATFORM_STORE_PATH"] = tempfile.mktemp(suffix=".db")
os.environ.pop("DATABASE_URL", None)
for _key in ("TRAVELC_USERNAME", "TRAVELC_PASSWORD", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
    os.environ.pop(_key, None)

import pytest


class FakeTravelCompositorAPI:
    """A minimal stand-in for TravelCompositorAPI, used wherever builder.py needs a client to
    resolve destinations/geolocations against. Every destination name given to
    resolve_destination() resolves successfully by default (name -> "DEST-<slug>") unless it
    is listed in `unresolvable`, which mirrors a real "this place isn't in Travel
    Compositor's destination list yet" case builder.py has explicit handling for.
    """

    def __init__(self, unresolvable=None):
        self.unresolvable = set(unresolvable or [])
        self.calls = []  # every (method, args) call made, for tests that want to assert on it

    def _code_for(self, name):
        return "DEST-" + "".join(ch for ch in name.upper() if ch.isalnum())[:12]

    def resolve_destination(self, query_term):
        self.calls.append(("resolve_destination", query_term))
        if query_term in self.unresolvable:
            return {"valid": False, "tc_code": None, "name": None}
        return {"valid": True, "tc_code": self._code_for(query_term), "name": query_term}

    def resolve_destinations_bulk(self, terms):
        return [self.resolve_destination(t) for t in terms]

    def resolve_transfer_zone_geolocation(self, supplier_id, city):
        self.calls.append(("resolve_transfer_zone_geolocation", supplier_id, city))
        return {"valid": False}  # forces builder.py's tests to supply manual_latitude/longitude

    def resolve_destination_geolocation(self, query_term):
        self.calls.append(("resolve_destination_geolocation", query_term))
        return {"valid": False}

    def get_closed_tours(self, *a, **kw):
        return []

    def get_tickets(self, *a, **kw):
        return []


@pytest.fixture
def fake_api_client():
    return FakeTravelCompositorAPI()


@pytest.fixture
def fake_api_client_factory():
    """For tests that need to control which destination names fail to resolve."""
    return FakeTravelCompositorAPI


@pytest.fixture(autouse=True)
def _reset_outreach_discovery_circuit_breaker():
    """CONFIRMED TEST-ISOLATION FIX (full-app audit Batch 4, 2026-09-02): outreach_discovery's
    provider circuit breaker (added to stop a dead provider being re-hit on every query within
    one real run - see its own module-level comment) is deliberately process-lifetime state, the
    same pattern geocoding_client's failure cache and platform_store's health cache already use.
    Left un-reset, a test that deliberately simulates a 429/quota error trips it for real, and
    every LATER test in the same pytest session then sees that provider as still "tripped" - a
    cross-test leak with no relationship to the app's own behavior. Reset before and after every
    test so each one starts and ends with a clean breaker state, regardless of import order."""
    import outreach_discovery as _od
    _od.reset_circuit_breakers()
    yield
    _od.reset_circuit_breakers()
