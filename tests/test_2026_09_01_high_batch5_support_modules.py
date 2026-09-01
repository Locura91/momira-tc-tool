"""Tests for the fifth (and final HIGH) batch of the full-app audit, fixed 2026-09-01: the
support-module HIGH findings.

  1. state_store.StateStore.upsert_state() discarded platform_store.set()'s failure signal -
     the exact silent re-billing this module's own docstring calls out. Fixed: it now returns
     the success flag and prints a loud, specific failure when the write itself fails.
  2. cancellation_bulk_transport.apply_proposals() only rewrote the EN datasheet's cancellation
     paragraph - every other language kept describing the OLD policy, and the translation
     tracker's own self-healing "verify existing content" check then permanently marked that
     stale text as already-translated. Fixed: the new cancellation text is now swapped into
     every language's datasheet too, and the entity's translation-tracker state is explicitly
     cleared so the next sync doesn't trust stale content as already-correct.
  3. geocoding_client cached a failed lookup (network error, not a genuine "no results")
     FOREVER, turning one transient blip into a permanent "this place does not exist." Fixed:
     a failed attempt is only cached for 300 seconds and retried after that, while a genuine
     empty answer is still cached indefinitely, same as before.
  4. supplier_images.resolve_and_host_image() collapsed "nothing saved yet" and "an image WAS
     saved but the R2 upload failed" into the exact same (None, direction) shape. Fixed: a
     third element, the upload error, now distinguishes the two, and app.py's review screen
     shows a different message for each.
  5. ui_components.render_seasonal_price_editor() wrote its fabricated "Example row - edit or
     delete" $0 placeholder directly into the live price_list data on every render, before an
     operator touched anything - defeating every "add at least one price row" guard downstream.
     Fixed: the placeholder is now display-only; target_data["price_list"] is only ever changed
     by an operator's actual save. app.py's "Add another Modality" publish loop also gained a
     real price-row check it never had (it only checked that `data` was present at all).

Uses the same offline platform_store isolation every other durable-storage test relies on
(conftest.py: PLATFORM_STORE_PATH is a fresh temp SQLite file, no DATABASE_URL).
"""
import io
import os
import time
import uuid

import cancellation_bulk_transport as cbt
import geocoding_client as gc
import platform_store
import supplier_images as si
import ui_components as ui
from state_store import StateStore

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


# ======================================================================
# 1. state_store.upsert_state discarding the write-failure signal
# ======================================================================
def test_upsert_state_returns_true_on_a_real_successful_write():
    store = StateStore()
    ok = store.upsert_state("transfer", "SUP-BATCH5", f"entity-{uuid.uuid4().hex[:8]}",
                            "hash123", ["DE", "FR"])
    assert ok is True


def test_upsert_state_returns_false_and_logs_when_the_write_fails(monkeypatch, capsys):
    monkeypatch.setattr(platform_store, "set", lambda *a, **kw: False)
    store = StateStore()
    ok = store.upsert_state("transfer", "SUP-BATCH5-FAIL", "entity-x", "hash123", ["DE"])
    assert ok is False
    captured = capsys.readouterr()
    assert "FAILED to record translation state" in captured.out
    assert "SUP-BATCH5-FAIL" in captured.out


def test_clear_state_deletes_the_tracked_row():
    store = StateStore()
    entity_id = f"entity-{uuid.uuid4().hex[:8]}"
    store.upsert_state("transport", "SUP-BATCH5-CLEAR", entity_id, "hash1", ["DE"])
    assert store.get_state("transport", "SUP-BATCH5-CLEAR", entity_id) is not None
    assert store.clear_state("transport", "SUP-BATCH5-CLEAR", entity_id) is True
    assert store.get_state("transport", "SUP-BATCH5-CLEAR", entity_id) is None


# ======================================================================
# 2. Bulk Transport cancellation update freezing stale non-EN text
# ======================================================================
def _fake_client(update_calls):
    class _Client:
        def update_transport(self, supplier_id, payload):
            update_calls.append(payload)
            return {"id": payload.get("id")}
    return _Client()


def test_apply_proposals_swaps_the_new_text_into_every_language_not_just_en():
    raw = {
        "id": "T-1",
        "datasheets": {
            "EN": {"description": "<p>Service info</p><p>Free cancellation up to 30 days before.</p>"},
            "DE": {"description": "<p>Serviceinfo</p><p>Kostenlose Stornierung bis 30 Tage vorher.</p>"},
            "FR": {"description": "<p>Infos service</p><p>Annulation gratuite jusqu'a 30 jours avant.</p>"},
        },
    }
    proposal = {
        "id": "T-1", "name": "Test Transport",
        "new_ranges_wire": [{"days": 14, "percentage": 100.0, "isBeforeStart": True}],
        "new_cancellation_text": "Free cancellation up to 14 days before departure.",
        "new_description_html": "<p>Service info</p><p>Free cancellation up to 14 days before departure.</p>",
        "raw": raw,
    }
    update_calls = []
    results = cbt.apply_proposals(_fake_client(update_calls), "SUP-1", [proposal])
    assert results[0]["ok"] is True
    sent_datasheets = update_calls[0]["datasheets"]
    for lang in ("EN", "DE", "FR"):
        assert "14 days before departure" in sent_datasheets[lang]["description"], (
            f"{lang} datasheet must carry the NEW cancellation text - leaving it behind is "
            f"exactly the stale-policy bug this fix closes"
        )
        # The other paragraph (service info) must be untouched.
    assert "Serviceinfo" in sent_datasheets["DE"]["description"]
    assert "Infos service" in sent_datasheets["FR"]["description"]


def test_apply_proposals_clears_the_translation_tracker_state_on_success():
    entity_id = f"T-{uuid.uuid4().hex[:8]}"
    StateStore().upsert_state("transport", "SUP-2", entity_id, "old-hash", ["DE", "FR"])
    assert StateStore().get_state("transport", "SUP-2", entity_id) is not None

    raw = {"id": entity_id, "datasheets": {"EN": {"description": "<p>Info</p><p>Old cancellation text.</p>"}}}
    proposal = {
        "id": entity_id, "name": "Another Transport",
        "new_ranges_wire": [], "new_cancellation_text": "New cancellation text.",
        "new_description_html": "<p>Info</p><p>New cancellation text.</p>", "raw": raw,
    }
    results = cbt.apply_proposals(_fake_client([]), "SUP-2", [proposal])
    assert results[0]["ok"] is True
    assert StateStore().get_state("transport", "SUP-2", entity_id) is None, (
        "a successful bulk cancellation update must invalidate this Transport's translation "
        "tracker state, so the next sync doesn't trust stale non-EN text as already correct"
    )


def test_apply_proposals_does_not_invalidate_state_on_a_failed_update():
    class _FailingClient:
        def update_transport(self, supplier_id, payload):
            return {"error": "simulated failure"}
    entity_id = f"T-{uuid.uuid4().hex[:8]}"
    StateStore().upsert_state("transport", "SUP-3", entity_id, "old-hash", ["DE"])
    raw = {"id": entity_id, "datasheets": {"EN": {"description": "<p>Info</p><p>Old text.</p>"}}}
    proposal = {"id": entity_id, "name": "X", "new_ranges_wire": [],
               "new_cancellation_text": "New text.", "new_description_html": "<p>Info</p><p>New text.</p>",
               "raw": raw}
    results = cbt.apply_proposals(_FailingClient(), "SUP-3", [proposal])
    assert results[0]["ok"] is False
    assert StateStore().get_state("transport", "SUP-3", entity_id) is not None, (
        "a FAILED publish must not clear tracker state for content that was never actually "
        "changed on the live record"
    )


# ======================================================================
# 3. Geocode failures cached forever
# ======================================================================
def test_a_failed_lookup_is_retried_after_the_failure_window_not_cached_forever(monkeypatch):
    query = f"nowhere-{uuid.uuid4().hex[:8]}"
    calls = {"n": 0}

    def failing_nominatim(clean_query, limit):
        calls["n"] += 1
        return [], False

    def failing_photon(clean_query, limit):
        return [], False

    monkeypatch.setattr(gc, "_nominatim_search", failing_nominatim)
    monkeypatch.setattr(gc, "_photon_search", failing_photon)
    monkeypatch.setattr(gc, "_FAILURE_RETRY_SECONDS", 0)  # expire immediately for the test

    gc.geocode_search(query, limit=1)
    gc.geocode_search(query, limit=1)
    assert calls["n"] == 2, (
        "a genuinely failed attempt must not be cached forever - with the retry window "
        "expired, the SECOND call must hit the (fake) providers again, not reuse a stale [] "
        "from the first failure"
    )


def test_a_failed_lookup_is_not_retried_within_the_failure_window(monkeypatch):
    query = f"blocked-{uuid.uuid4().hex[:8]}"
    calls = {"n": 0}

    def failing_nominatim(clean_query, limit):
        calls["n"] += 1
        return [], False

    monkeypatch.setattr(gc, "_nominatim_search", failing_nominatim)
    monkeypatch.setattr(gc, "_photon_search", lambda q, limit: ([], False))
    monkeypatch.setattr(gc, "_FAILURE_RETRY_SECONDS", 300)

    gc.geocode_search(query, limit=1)
    gc.geocode_search(query, limit=1)
    assert calls["n"] == 1, (
        "within the retry window, a repeated lookup for the same failed query must not "
        "re-hit the network every time (would defeat the whole point of caching, and hammer "
        "a rate-limited provider) - it must reuse the recent failure"
    )


def test_a_genuine_empty_result_is_still_cached_indefinitely(monkeypatch):
    query = f"reallynowhere-{uuid.uuid4().hex[:8]}"
    nominatim_calls = {"n": 0}
    photon_calls = {"n": 0}

    def nominatim_ok_but_empty(clean_query, limit):
        nominatim_calls["n"] += 1
        return [], True  # the call itself succeeded; there's just genuinely nothing there

    def photon_should_not_be_reached(clean_query, limit):
        photon_calls["n"] += 1
        return [], True

    monkeypatch.setattr(gc, "_nominatim_search", nominatim_ok_but_empty)
    monkeypatch.setattr(gc, "_photon_search", photon_should_not_be_reached)

    gc.geocode_search(query, limit=1)
    gc.geocode_search(query, limit=1)
    assert nominatim_calls["n"] == 1, "a real (not failed) empty answer must still be cached, unchanged from before"


# ======================================================================
# 4. R2 upload failure indistinguishable from "nothing saved"
# ======================================================================
def test_resolve_and_host_image_distinguishes_upload_failure_from_nothing_saved(monkeypatch):
    supplier_id = f"SUP-BATCH5-{uuid.uuid4().hex[:8]}"
    si.set_supplier_image(supplier_id, "Transport", si.DIRECTION_AIRPORT_TO_HOTEL, b"bytes", "jpg")

    import r2_client
    monkeypatch.setattr(r2_client, "upload_image",
                        lambda image_bytes, filename="image.jpg": (_ for _ in ()).throw(
                            RuntimeError("bad R2 credentials")))

    url_with_saved_image, direction1, error1 = si.resolve_and_host_image(
        supplier_id, "Transport", "Hurghada Airport", "Steigenberger Hotel")
    assert url_with_saved_image is None
    assert error1 is not None and "bad R2 credentials" in error1

    url_nothing_saved, direction2, error2 = si.resolve_and_host_image(
        f"{supplier_id}-nothing-saved", "Transport", "Hurghada Airport", "Steigenberger Hotel")
    assert url_nothing_saved is None
    assert error2 is None, "no image was ever saved here - there is no upload failure to report"
    assert direction1 == direction2  # same route, same classified direction either way


def test_app_py_shows_a_distinct_error_for_an_upload_failure_not_the_nothing_saved_message():
    source = _read_app_py()
    idx = source.index('elif current.get("_image_upload_error"):')
    window = source[idx:idx + 1600]
    assert "st.error" in window
    assert "Re-uploading it in Setup won't fix this" in window


# ======================================================================
# 5. The "Example row" placeholder leaking into live price-list data
# ======================================================================
def test_render_seasonal_price_editor_never_writes_a_placeholder_into_target_data(monkeypatch):
    """Can't drive the real Streamlit widget in this suite (no test harness - same constraint
    noted throughout this codebase's other ui_components tests), so this stubs just enough of
    `streamlit` to let the function run through its early rendering calls and confirms the
    thing that matters: target_data["price_list"] is never mutated with the fabricated
    placeholder before an operator saves anything."""
    import types
    import pandas as pd

    fake_st = types.SimpleNamespace(
        data_editor=lambda *a, **kw: kw.get("value", a[0] if a else None),
        session_state={},
    )
    monkeypatch.setattr(ui, "st", fake_st, raising=False)
    monkeypatch.setattr(ui, "editable_table", lambda label, df, key, on_save=None: None)

    target_data = {}  # nothing entered yet - the exact case that used to get the placeholder
    ui.render_seasonal_price_editor("Pricing - Test", target_data, "test_pricing_key", "EUR")

    assert target_data.get("price_list") in (None, []), (
        "target_data['price_list'] must stay empty until an operator actually saves - the "
        "fabricated 'Example row' placeholder must never be written into live data just "
        "because the widget rendered"
    )


def test_extra_modality_publish_loop_checks_for_a_real_price_row():
    source = _read_app_py()
    idx = source.index('for mod in extra_modalities:')
    window = source[idx:idx + 2000]
    assert 'not (mod.get("data") or {}).get("price_list")' in window, (
        "the 'Add another Modality' publish loop must check for at least one real price row, "
        "not just that `data` is present at all (which was always true, placeholder included)"
    )
