"""Regression tests for the 2026-10-02 weekly duplicate-code audit: the "bare list, or a dict
wrapping the list under one of a few likely keys" shape-sniffing used by app_helpers'
get_existing_tour_names/get_existing_ticket_codes/get_existing_hotel_names and
translation_tool._normalize_closed_tour_list was hand-copied 4 times (translation_tool.py's own
docstring already flagged it as a deliberate copy of app_helpers' logic). Moved to
list_response_utils.normalize_list_response, a standalone module so app_helpers.py and
translation_tool.py can both import it without any circular-import risk (app_helpers.py already
imports from translation_tool.py at module level)."""
import app_helpers
import translation_tool
from list_response_utils import normalize_list_response


def test_bare_list_passed_through_unchanged():
    items = [{"code": "A"}, {"code": "B"}]
    assert normalize_list_response(items, ("tours",)) == items


def test_wrapped_dict_unwraps_first_matching_key():
    result = {"closedTours": [{"code": "A"}]}
    assert normalize_list_response(result, ("closedTour", "closedTours", "items")) == [{"code": "A"}]


def test_wrapped_dict_tries_keys_in_order():
    result = {"items": [{"code": "fallback"}], "data": [{"code": "wrong"}]}
    # "data" appears first in the tuple here, so it should win over "items".
    assert normalize_list_response(result, ("data", "items")) == [{"code": "wrong"}]


def test_unrecognized_shape_returns_empty_list():
    assert normalize_list_response({"somethingElse": "nope"}, ("hotel", "hotels")) == []
    assert normalize_list_response(None, ("hotel", "hotels")) == []
    assert normalize_list_response("a string", ("hotel", "hotels")) == []


def test_app_helpers_getters_use_the_shared_normalizer():
    import inspect
    for fn in (app_helpers.get_existing_tour_names, app_helpers.get_existing_ticket_codes,
               app_helpers.get_existing_hotel_names):
        src = inspect.getsource(fn)
        assert "normalize_list_response(" in src
        assert "for key in (" not in src  # the old hand-copied loop should be gone


def test_translation_tool_normalize_closed_tour_list_uses_the_shared_normalizer():
    import inspect
    src = inspect.getsource(translation_tool._normalize_closed_tour_list)
    assert "normalize_list_response(" in src


def test_translation_tool_normalize_closed_tour_list_behavior_unchanged():
    result = {"closedTours": [{"code": "CT1", "name": "Nile Cruise"}, {"code": "", "name": "No code"}]}
    tours = translation_tool._normalize_closed_tour_list(result)
    assert tours == [{"code": "CT1", "name": "Nile Cruise"}]
