"""Regression tests for draft_autosave.py - a real product-owner request (2026-09-17):

    "before th publish button, humans must be able to change the modalities, to change the main
    information without loosing all the information and without starting from scratch."

Clarified in follow-up: "any go back and not only within closedtours. That is a general issue,
because if the human reloads the page all information is gone, if human wants to change the
error so far the human has to start all over."

Fix: draft_autosave.py persists a JSON-safe snapshot of st.session_state to platform_store (the
same durable, Postgres-backed store already used elsewhere in this app) under a draft id kept in
the page's own URL query string, so it survives a plain page reload. restore_and_autosave() is
called once near the top of app.py, before any flow-specific rendering, so this applies to every
product-type flow automatically, not just ClosedTour.

Uses a FAKE streamlit module object (session_state/query_params/UI calls) since draft_autosave.py
is a plain importable module with no top-level Streamlit script execution - unlike app.py, it
doesn't need the source-text-reading workaround the rest of this suite uses for app.py itself.
platform_store is the REAL module (against the test suite's isolated local SQLite file - see
conftest.py), matching every other durable-storage test in this suite.
"""
import pytest

import platform_store
import draft_autosave as da


class _StopSignal(Exception):
    """Raised by the fake st.stop() so a test can assert execution actually halted there,
    exactly like the real st.stop() halts the Streamlit script."""


class _RerunSignal(Exception):
    """Raised by the fake st.rerun() - same idea as _StopSignal."""


class _FakeColumn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeStreamlit:
    """Stands in for the `streamlit` module inside draft_autosave.py. session_state and
    query_params are plain dicts (everything draft_autosave.py does with them - .get/.pop/
    item assignment/.items() - works identically on a plain dict). button() returns whatever
    was pre-programmed for its key via `click`, so a test can simulate exactly one button being
    pressed without a real Streamlit runtime."""

    def __init__(self):
        self.session_state = {}
        self.query_params = {}
        self.warnings = []
        self._clicked_key = None

    def click(self, key):
        self._clicked_key = key

    def warning(self, msg):
        self.warnings.append(msg)

    def columns(self, n):
        return [_FakeColumn() for _ in range(n)]

    def button(self, label, **kwargs):
        return kwargs.get("key") == self._clicked_key

    def rerun(self):
        raise _RerunSignal()

    def stop(self):
        raise _StopSignal()


@pytest.fixture(autouse=True)
def _clean_namespace_and_fake_st(monkeypatch):
    """Clears the wizard_drafts namespace before/after (mirrors every other durable-storage
    test's isolation pattern - see test_2026_09_08_price_validity_tracking.py), and swaps in a
    fresh FakeStreamlit for st.session_state/query_params/UI calls."""
    for key in list(platform_store.get_namespace(da._NAMESPACE).keys()):
        platform_store.delete(da._NAMESPACE, key)
    fake_st = FakeStreamlit()
    monkeypatch.setattr(da, "st", fake_st)
    yield fake_st
    for key in list(platform_store.get_namespace(da._NAMESPACE).keys()):
        platform_store.delete(da._NAMESPACE, key)


# ---------------------------------------------------------------------------------------------
# _is_json_safe / _json_safe_snapshot
# ---------------------------------------------------------------------------------------------

def test_json_safe_accepts_ordinary_wizard_data():
    assert da._is_json_safe({"tour_name": "ASW-12", "nights": 12, "stop_sales": [{"start": "x"}]})
    assert da._is_json_safe(["a", 1, True, None])


def test_json_safe_rejects_non_serializable_values():
    assert not da._is_json_safe(b"raw image bytes")
    assert not da._is_json_safe(object())
    assert not da._is_json_safe(lambda: None)


def test_snapshot_excludes_bookkeeping_and_non_serializable_keys(_clean_namespace_and_fake_st):
    fake_st = _clean_namespace_and_fake_st
    fake_st.session_state.update({
        "extracted": {"tour_name": "ASW-12"},
        "cfg_currency": "USD",
        "_draft_restore_checked": True,       # this module's own bookkeeping - must be excluded
        "_draft_pending_restore": {"data": {}},  # ditto
        "client": object(),                    # a real API client instance - not JSON-safe
        "doc_raw_images": [("x.jpg", b"\xff\xd8")],  # raw image bytes - not JSON-safe
    })
    snapshot = da._json_safe_snapshot()
    assert snapshot == {"extracted": {"tour_name": "ASW-12"}, "cfg_currency": "USD"}


def test_snapshot_hash_is_stable_for_the_same_content_and_differs_when_content_changes():
    a = da._snapshot_hash({"x": 1, "y": 2})
    b = da._snapshot_hash({"y": 2, "x": 1})  # same content, different key order
    c = da._snapshot_hash({"x": 1, "y": 3})
    assert a == b
    assert a != c


# ---------------------------------------------------------------------------------------------
# get_draft_id
# ---------------------------------------------------------------------------------------------

def test_get_draft_id_generates_and_persists_one_into_the_url_query_string(_clean_namespace_and_fake_st):
    fake_st = _clean_namespace_and_fake_st
    assert "draft" not in fake_st.query_params
    first = da.get_draft_id()
    assert first
    assert fake_st.query_params["draft"] == first


def test_get_draft_id_reuses_an_existing_query_param(_clean_namespace_and_fake_st):
    fake_st = _clean_namespace_and_fake_st
    fake_st.query_params["draft"] = "already-here-123"
    assert da.get_draft_id() == "already-here-123"


# ---------------------------------------------------------------------------------------------
# discard_draft / clear_on_publish_success
# ---------------------------------------------------------------------------------------------

def test_discard_draft_removes_it_from_durable_storage(_clean_namespace_and_fake_st):
    platform_store.set(da._NAMESPACE, "d1", {"data": {"a": 1}})
    da.discard_draft("d1")
    assert platform_store.get(da._NAMESPACE, "d1") is None


def test_clear_on_publish_success_deletes_this_tabs_draft(_clean_namespace_and_fake_st):
    fake_st = _clean_namespace_and_fake_st
    fake_st.query_params["draft"] = "d2"
    platform_store.set(da._NAMESPACE, "d2", {"data": {"tour_code": "ASW-12"}})
    da.clear_on_publish_success()
    assert platform_store.get(da._NAMESPACE, "d2") is None


def test_clear_on_publish_success_is_a_noop_with_no_draft_id(_clean_namespace_and_fake_st):
    # Must not raise even if, somehow, restore_and_autosave() was never called first.
    da.clear_on_publish_success()


# ---------------------------------------------------------------------------------------------
# _autosave - the redundant-write skip
# ---------------------------------------------------------------------------------------------

def test_autosave_writes_the_current_snapshot(_clean_namespace_and_fake_st):
    fake_st = _clean_namespace_and_fake_st
    fake_st.session_state["extracted"] = {"tour_name": "ASW-12"}
    da._autosave("d3")
    saved = platform_store.get(da._NAMESPACE, "d3")
    assert saved["data"] == {"extracted": {"tour_name": "ASW-12"}}
    assert saved["saved_at"]


def test_autosave_skips_the_write_when_nothing_changed(_clean_namespace_and_fake_st, monkeypatch):
    fake_st = _clean_namespace_and_fake_st
    fake_st.session_state["extracted"] = {"tour_name": "ASW-12"}
    da._autosave("d4")

    calls = []
    real_set = platform_store.set
    monkeypatch.setattr(platform_store, "set", lambda *a, **k: (calls.append(1), real_set(*a, **k))[1])
    da._autosave("d4")  # identical snapshot - must NOT write again
    assert calls == []


def test_autosave_writes_again_once_state_actually_changes(_clean_namespace_and_fake_st, monkeypatch):
    fake_st = _clean_namespace_and_fake_st
    fake_st.session_state["extracted"] = {"tour_name": "ASW-12"}
    da._autosave("d5")

    calls = []
    real_set = platform_store.set
    monkeypatch.setattr(platform_store, "set", lambda *a, **k: (calls.append(1), real_set(*a, **k))[1])
    fake_st.session_state["extracted"]["tour_name"] = "ASW-13"
    da._autosave("d5")
    assert calls == [1]


def test_autosave_is_a_noop_on_an_empty_snapshot(_clean_namespace_and_fake_st):
    # An empty session_state (nothing JSON-safe yet) must not write an empty record that would
    # later be offered back as "unfinished work".
    da._autosave("d6")
    assert platform_store.get(da._NAMESPACE, "d6") is None


# ---------------------------------------------------------------------------------------------
# restore_and_autosave - the full first-rerun / restore-banner / autosave flow
# ---------------------------------------------------------------------------------------------

def test_fresh_session_with_no_saved_draft_just_autosaves(_clean_namespace_and_fake_st):
    fake_st = _clean_namespace_and_fake_st
    fake_st.session_state["cfg_currency"] = "USD"
    da.restore_and_autosave()  # must NOT raise/stop - nothing to restore
    draft_id = fake_st.query_params["draft"]
    saved = platform_store.get(da._NAMESPACE, draft_id)
    assert saved["data"] == {"cfg_currency": "USD"}


def test_fresh_session_with_a_saved_draft_shows_the_banner_and_stops(_clean_namespace_and_fake_st):
    fake_st = _clean_namespace_and_fake_st
    fake_st.query_params["draft"] = "existing-draft"
    platform_store.set(da._NAMESPACE, "existing-draft",
                        {"data": {"extracted": {"tour_name": "ASW-12"}}, "saved_at": "2026-09-17 10:00 UTC"})
    with pytest.raises(_StopSignal):
        da.restore_and_autosave()
    assert any("2026-09-17 10:00 UTC" in w for w in fake_st.warnings)
    # Nothing was restored into session_state yet - the human hasn't chosen anything.
    assert "extracted" not in fake_st.session_state


def test_clicking_restore_repopulates_session_state_and_reruns(_clean_namespace_and_fake_st):
    fake_st = _clean_namespace_and_fake_st
    fake_st.query_params["draft"] = "existing-draft"
    platform_store.set(da._NAMESPACE, "existing-draft",
                        {"data": {"extracted": {"tour_name": "ASW-12"}, "cfg_currency": "USD"}})
    fake_st.click("_draft_restore_btn")
    with pytest.raises(_RerunSignal):
        da.restore_and_autosave()
    assert fake_st.session_state["extracted"] == {"tour_name": "ASW-12"}
    assert fake_st.session_state["cfg_currency"] == "USD"
    assert da._PENDING_RESTORE_KEY not in fake_st.session_state
    # The draft must still exist in durable storage - restoring is not the same as discarding.
    assert platform_store.get(da._NAMESPACE, "existing-draft") is not None


def test_clicking_discard_deletes_the_draft_and_reruns(_clean_namespace_and_fake_st):
    fake_st = _clean_namespace_and_fake_st
    fake_st.query_params["draft"] = "existing-draft"
    platform_store.set(da._NAMESPACE, "existing-draft", {"data": {"extracted": {"tour_name": "ASW-12"}}})
    fake_st.click("_draft_discard_btn")
    with pytest.raises(_RerunSignal):
        da.restore_and_autosave()
    assert platform_store.get(da._NAMESPACE, "existing-draft") is None
    # Nothing was restored into session_state.
    assert "extracted" not in fake_st.session_state


def test_second_rerun_of_the_same_session_does_not_re_check_storage_for_a_new_draft(_clean_namespace_and_fake_st, monkeypatch):
    # Once a session has already been through the restore check once (_RESTORE_CHECKED_KEY set),
    # a LATER draft saved under the same id by a different tab/process must not suddenly pop up
    # as a restore banner mid-session - only checked once, right at the start.
    fake_st = _clean_namespace_and_fake_st
    fake_st.query_params["draft"] = "d7"
    fake_st.session_state[da._RESTORE_CHECKED_KEY] = True
    platform_store.set(da._NAMESPACE, "d7", {"data": {"extracted": {"tour_name": "someone else's tour"}}})
    da.restore_and_autosave()  # must NOT raise/stop
    assert "extracted" not in fake_st.session_state
