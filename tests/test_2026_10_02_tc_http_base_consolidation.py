"""Regression tests for the 2026-10-02 weekly duplicate-code audit, Phase 2: api_client.py and
travelcompositor_api.py's low-level HTTP plumbing (auth, retry, network-error handling, response
parsing - everything except their own endpoint methods, which remain deliberately separate per
translation_tool.py's "NOTE ON THE TWO API CLIENTS") moved to tc_http_base.TravelCompositorHTTPBase.

These pin that both client classes still behave exactly as before - not re-test business logic
the existing api_client/translation-sync test files already cover.
"""
import inspect

import api_client
import tc_http_base
import travelcompositor_api


def test_both_clients_inherit_the_shared_http_base():
    assert issubclass(api_client.TravelCompositorAPI, tc_http_base.TravelCompositorHTTPBase)
    assert issubclass(travelcompositor_api.TravelCompositorAPI, tc_http_base.TravelCompositorHTTPBase)


def test_both_clients_share_the_same_plumbing_methods_not_separate_copies():
    for name in ("authenticate", "get_headers", "_network_error_response", "_request", "_json",
                 "_handle_response", "_handle_list_response"):
        assert getattr(api_client.TravelCompositorAPI, name) is getattr(tc_http_base.TravelCompositorHTTPBase, name)
        assert getattr(travelcompositor_api.TravelCompositorAPI, name) is getattr(tc_http_base.TravelCompositorHTTPBase, name)
    assert api_client.TravelCompositorAPI._TRANSIENT_STATUS_CODES == {408, 429, 500, 502, 503, 504, 599}
    assert travelcompositor_api.TravelCompositorAPI._TRANSIENT_STATUS_CODES == {408, 429, 500, 502, 503, 504, 599}


def test_api_client_keeps_its_extra_cache_fields_on_init():
    """api_client.TravelCompositorAPI has extra per-call caches (transfer zones, transport base)
    that travelcompositor_api.TravelCompositorAPI has never needed - these must survive the move
    to super().__init__(), not get lost or accidentally shared onto the other class."""
    a = api_client.TravelCompositorAPI()
    assert a._transfer_zone_cache == {}
    assert a._transport_base_cache is None

    b = travelcompositor_api.TravelCompositorAPI()
    assert not hasattr(b, "_transfer_zone_cache")
    assert not hasattr(b, "_transport_base_cache")


def test_both_clients_get_the_common_fields_from_env_defaults():
    a = api_client.TravelCompositorAPI()
    b = travelcompositor_api.TravelCompositorAPI()
    for client in (a, b):
        assert client.api_base_url == "https://online.travelcompositor.com/resources"
        assert client.microsite_id == "momiratravel"
        assert client.auth_token is None
        assert client._destination_cache is None


def test_travelcompositor_api_now_has_a_json_safe_parsing_guard_it_previously_lacked():
    """CONFIRMED FIX folded in during this consolidation: travelcompositor_api.py's own prior
    docstring explicitly flagged having no _json() wrapper as an accepted-but-not-deliberate gap.
    Both clients should now raise the same friendly RuntimeError (not a raw JSONDecodeError) on a
    malformed response body."""
    import requests
    res = requests.Response()
    res.status_code = 200
    res._content = b"not valid json"

    for cls in (api_client.TravelCompositorAPI, travelcompositor_api.TravelCompositorAPI):
        instance = cls()
        try:
            instance._json(res)
            assert False, "expected RuntimeError"
        except RuntimeError as e:
            assert "wasn't valid JSON" in str(e)


def test_network_error_response_is_a_synthetic_599_shared_by_both_clients():
    exc = ConnectionError("boom")
    for cls in (api_client.TravelCompositorAPI, travelcompositor_api.TravelCompositorAPI):
        res = cls._network_error_response(exc)
        assert res.status_code == 599
        body = res.json()
        assert body["error"] == "network_error"
        assert "boom" in body["message"]


def test_request_signature_unchanged_for_both_clients():
    for cls in (api_client.TravelCompositorAPI, travelcompositor_api.TravelCompositorAPI):
        sig = inspect.signature(cls._request)
        assert list(sig.parameters) == ["self", "method", "url", "kwargs"]
