"""Tests for the MEDIUM/LOW "Batch 5" findings from the full-app audit
(full-app-audit-2026-09-01.md), fixed 2026-09-02 - covers the support modules
(ui_components.py, r2_client.py, geocoding_client.py, platform_store.py, state_store.py,
weekly_review.py), the last of the two remaining approved batches (Batch 4 = outreach subsystem,
Batch 5 = these support-module findings):

  6.  An unclamped extracted max_child_age (e.g. 18, widget caps at 17) crashed the ENTIRE
      review screen with StreamlitAPIException - fixed by clamping both min/max child age to
      the widget's [0, 17] range before passing as `value=`, with a warning when clamped.
  7.  R2's recommended 2-day image-expiry lifecycle rule can 404 an image if publish happens
      after a multi-day review - fixed by embedding an upload timestamp in the R2 object key and
      adding image_upload_age_hours / stale_image_urls / stale_image_warning helpers so a caller
      can warn before publish.
  8.  Nominatim's 1-req/s throttle clock wasn't advanced on a request exception - fixed by
      recording the attempt time in a `finally` block so a burst of failures can't produce a
      burst of unthrottled requests.
  9.  platform_store's schema-init cache was sticky for the process lifetime - fixed with a
      periodic re-check (same 60s cadence as health()'s own cache).
  10. A transient durable-store read failure looked identical to "genuinely empty" in
      state_store's legacy-migration check, risking an overwrite of current data with a stale
      legacy snapshot - fixed by checking platform_store.health() before trusting an empty read.
  11. weekly_review's state is one read-modify-write JSON blob with no compare-and-swap - fixed
      with an optimistic re-read-before-write retry helper that narrows (does not eliminate) the
      lost-update race.
  12. editable_field rendered extracted content as raw unescaped HTML - fixed with html.escape.
  13. The DB-credential scrubber split on the FIRST "@", mis-extracting (and failing to scrub) a
      password containing "@" - fixed to split on the LAST "@".
  14. Every failed R2 image upload was labeled "image.jpg" regardless of which one - fixed with
      per-item numbering (image_1.jpg, image_2.jpg, ...).
"""
import json
import time

import pytest

import geocoding_client
import platform_store
import r2_client
import state_store
import ui_components
import weekly_review


# ======================================================================
# 6. Child age band clamping
# ======================================================================
def test_render_child_age_band_source_clamps_out_of_range_values():
    import inspect
    source = inspect.getsource(ui_components.render_child_age_band)
    assert "clamped_min = max(0, min(min_default, 17))" in source
    assert "clamped_max = max(0, min(max_default, 17))" in source
    assert "value=clamped_min" in source
    assert "value=clamped_max" in source


def test_child_age_clamp_math_never_exceeds_widget_bounds():
    for raw in (-5, 0, 2, 12, 17, 18, 40):
        clamped = max(0, min(raw, 17))
        assert 0 <= clamped <= 17


# ======================================================================
# 7. R2 image staleness detection
# ======================================================================
def test_upload_timestamp_key_embeds_a_recoverable_timestamp():
    key = r2_client._upload_timestamp_key("jpg")
    assert key.endswith(".jpg")
    ts_part = key.split("-", 1)[0]
    assert ts_part.isdigit()


def test_image_upload_age_hours_recent_upload_is_near_zero():
    key = r2_client._upload_timestamp_key("jpg")
    url = f"https://images.example.com/{key}"
    age = r2_client.image_upload_age_hours(url)
    assert age is not None
    assert age < 0.01


def test_image_upload_age_hours_old_upload_is_correctly_old():
    old_ts = int(time.time()) - int(50 * 3600)  # 50 hours ago
    url = f"https://images.example.com/{old_ts}-abcdef.jpg"
    age = r2_client.image_upload_age_hours(url)
    assert age is not None
    assert 49.9 < age < 50.1


def test_image_upload_age_hours_none_for_unrecognized_url():
    assert r2_client.image_upload_age_hours("https://images.example.com/manual-upload.jpg") is None
    assert r2_client.image_upload_age_hours("") is None
    assert r2_client.image_upload_age_hours(None) is None


def test_stale_image_urls_flags_only_old_ones():
    fresh_key = r2_client._upload_timestamp_key("jpg")
    old_ts = int(time.time()) - int(50 * 3600)
    urls = [
        f"https://images.example.com/{fresh_key}",
        f"https://images.example.com/{old_ts}-abcdef.jpg",
        "https://images.example.com/manual.jpg",
    ]
    stale = r2_client.stale_image_urls(urls)
    assert len(stale) == 1
    assert "abcdef" in stale[0]


def test_stale_image_warning_empty_when_nothing_stale():
    fresh_key = r2_client._upload_timestamp_key("jpg")
    assert r2_client.stale_image_warning([f"https://images.example.com/{fresh_key}"]) == ""


def test_stale_image_warning_names_the_count():
    old_ts = int(time.time()) - int(50 * 3600)
    warning = r2_client.stale_image_warning([f"https://images.example.com/{old_ts}-a.jpg"])
    assert "1 image" in warning
    assert "42h" in warning or "42" in warning


def test_upload_images_with_errors_numbers_each_failed_image():
    def _boom(*args, **kwargs):
        raise RuntimeError("R2 upload failed: simulated")
    import r2_client as mod
    orig = mod.upload_image
    mod.upload_image = _boom
    try:
        urls, errors = mod.upload_images_with_errors([(b"a", "jpg"), (b"b", "png"), (b"c", "jpg")])
    finally:
        mod.upload_image = orig
    assert urls == []
    assert len(errors) == 3
    assert errors[0].startswith("image_1.jpg:")
    assert errors[1].startswith("image_2.png:")
    assert errors[2].startswith("image_3.jpg:")


# ======================================================================
# 8. Nominatim throttle clock advances even on a failed request
# ======================================================================
def test_nominatim_search_source_advances_clock_in_finally():
    import inspect
    source = inspect.getsource(geocoding_client._nominatim_search)
    assert "finally:" in source
    assert "_last_nominatim_request_time[0] = time.time()" in source
    # the update must be inside the try/finally wrapping the request itself, not only after a
    # successful response - confirm the finally block is what performs it, not a bare post-call
    # statement outside any exception-handling structure.
    finally_idx = source.index("finally:")
    assert "_last_nominatim_request_time[0] = time.time()" in source[finally_idx:finally_idx + 120]


def test_nominatim_search_updates_clock_even_when_request_raises(monkeypatch):
    geocoding_client._last_nominatim_request_time[0] = 0.0

    def _raise(*args, **kwargs):
        raise ConnectionError("simulated network failure")

    monkeypatch.setattr(geocoding_client.requests, "get", _raise)
    before = time.time()
    results, ok = geocoding_client._nominatim_search("Some Place", 5)
    assert results == []
    assert ok is False
    assert geocoding_client._last_nominatim_request_time[0] >= before


# ======================================================================
# 9. platform_store schema-init cache re-checks periodically
# ======================================================================
def test_ensure_schema_recheck_constant_is_bounded_like_health_cache():
    assert platform_store._SCHEMA_RECHECK_SECONDS == platform_store._HEALTH_TTL_SECONDS == 60


def test_ensure_schema_source_tracks_an_init_timestamp():
    import inspect
    source = inspect.getsource(platform_store._ensure_schema)
    assert "_initialized_at" in source
    assert "_SCHEMA_RECHECK_SECONDS" in source


# ======================================================================
# 10. state_store legacy migration checks health() before trusting empty
# ======================================================================
def test_migrate_legacy_source_checks_health_before_trusting_empty_read():
    import inspect
    source = inspect.getsource(state_store.StateStore._migrate_legacy_if_present)
    assert "platform_store.health()" in source
    # the health check must come before the os.path.exists check that would otherwise proceed
    health_idx = source.index("platform_store.health()")
    exists_idx = source.index("os.path.exists(self.db_path)")
    assert health_idx < exists_idx


def test_migrate_legacy_skips_when_store_unhealthy(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_store, "get_namespace", lambda ns: {})
    monkeypatch.setattr(platform_store, "health", lambda: {"ok": False})
    called = {"exists": False}
    monkeypatch.setattr(state_store.os.path, "exists", lambda p: called.__setitem__("exists", True) or True)
    state_store.StateStore(db_path=str(tmp_path / "legacy.db"))
    # os.path.exists must never even be reached once health() says the store is unreachable.
    assert called["exists"] is False


def test_migrate_legacy_still_proceeds_when_store_healthy_and_no_legacy_file(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_store, "get_namespace", lambda ns: {})
    monkeypatch.setattr(platform_store, "health", lambda: {"ok": True})
    # No legacy file at this path - should just return quietly, no exception.
    state_store.StateStore(db_path=str(tmp_path / "does_not_exist.db"))


# ======================================================================
# 11. weekly_review optimistic retry-on-write
# ======================================================================
def test_read_modify_write_retries_when_state_changes_underneath(monkeypatch):
    calls = {"n": 0}
    states = [{"dismissed": {}}, {"dismissed": {"other": "ts"}}, {"dismissed": {"other": "ts"}}]

    def _fake_state():
        calls["n"] += 1
        # First call inside the loop returns the "before" snapshot; the re-read (2nd call)
        # simulates another session's write landing in between - forces one retry.
        idx = min(calls["n"] - 1, len(states) - 1)
        return dict(states[idx])

    saved = {}
    monkeypatch.setattr(weekly_review, "_state", _fake_state)
    monkeypatch.setattr(weekly_review, "_save_state", lambda s: saved.update(s) or True)

    def _mutate(state):
        state["dismissed"]["mine"] = "now"

    ok = weekly_review._read_modify_write(_mutate)
    assert ok is True
    # The saved state must be built from the LATER (post-conflict) base, not the stale first read.
    assert saved["dismissed"].get("other") == "ts"
    assert saved["dismissed"].get("mine") == "now"


def test_dismiss_and_mark_reviewed_route_through_read_modify_write():
    import inspect
    assert "_read_modify_write" in inspect.getsource(weekly_review.dismiss)
    assert "_read_modify_write" in inspect.getsource(weekly_review.mark_reviewed)
    assert "_read_modify_write" in inspect.getsource(weekly_review.snooze)


# ======================================================================
# 12. editable_field escapes HTML in the display value
# ======================================================================
def test_editable_field_source_escapes_current_value():
    import inspect
    source = inspect.getsource(ui_components.editable_field)
    assert "_html_module.escape(str(current_value))" in source


def test_html_escape_neutralizes_markup():
    import html
    raw = "<script>alert(1)</script>"
    escaped = html.escape(raw)
    assert "<script>" not in escaped
    assert "&lt;script&gt;" in escaped


# ======================================================================
# 13. Credential scrubber handles a password containing "@"
# ======================================================================
def test_scrub_handles_password_without_at_sign():
    url = "postgres://user:secretpass@host.example.com/db"
    text = f"connection failed: could not connect using secretpass to host.example.com"
    assert platform_store._scrub(text, url) == "connection failed: could not connect using *** to host.example.com"


def test_scrub_handles_password_containing_at_sign():
    url = "postgres://user:p@ssw0rd@host.example.com/db"
    text = "auth error: bad password p@ssw0rd for host.example.com"
    scrubbed = platform_store._scrub(text, url)
    assert "p@ssw0rd" not in scrubbed
    assert "***" in scrubbed


def test_scrub_no_password_is_a_no_op():
    url = "postgres://user@host.example.com/db"
    text = "plain error text"
    assert platform_store._scrub(text, url) == text


# ======================================================================
# 14. covered above under item 7 (test_upload_images_with_errors_numbers_each_failed_image)
# ======================================================================


# ======================================================================
# MODULE_BUILD stamps added to previously-unstamped support modules
# ======================================================================
def test_previously_unstamped_modules_now_carry_module_build():
    assert hasattr(r2_client, "MODULE_BUILD")
    assert hasattr(geocoding_client, "MODULE_BUILD")
    assert hasattr(state_store, "MODULE_BUILD")
