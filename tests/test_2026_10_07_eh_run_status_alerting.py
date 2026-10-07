"""
Tests for eh_run_status.py — the thing that makes a silent scheduled run visible.

The point of this module is that a scheduled task has nobody watching it, so these tests are
mostly about the failure modes that LOOK like success: a reading that quietly says everything is
closed, and a scheduler that quietly stopped running. Both must reach a human.
"""
import datetime as dt

import eh_run_status as ehs


UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 7, 9, 0, tzinfo=UTC)


# ----------------------------------------------------------------------
# assess_tour — the absolute guard
# ----------------------------------------------------------------------
def test_everything_closed_is_treated_as_a_broken_reader_not_a_real_closure():
    """The expensive silent failure: a parser breaks, every day reads FULL, and a
    successful-looking run blocks the entire year."""
    v = ehs.assess_tour("HKT-2", blocked_days=305, scanned_days=305)
    assert v["suspicious"]
    assert "page" in v["reason"].lower() or "closed" in v["reason"].lower()


def test_a_normal_heavy_but_not_total_block_is_not_suspicious():
    """HKT-3 genuinely blocks roughly half its window. That must not trip the alarm, or the alarm
    becomes noise and gets ignored."""
    assert not ehs.assess_tour("HKT-3", blocked_days=150, scanned_days=305)["suspicious"]


def test_a_small_block_is_not_suspicious():
    assert not ehs.assess_tour("CNX-3", blocked_days=3, scanned_days=320)["suspicious"]


def test_an_empty_scan_window_is_suspicious_rather_than_silently_fine():
    """Zero scanned days means nothing was checked - reporting that as 'nothing is closed' would
    be the same false reassurance."""
    assert ehs.assess_tour("CNX-3", blocked_days=0, scanned_days=0)["suspicious"]


# ----------------------------------------------------------------------
# assess_tour — the relative guard
# ----------------------------------------------------------------------
def test_a_sudden_jump_against_the_previous_run_is_suspicious():
    v = ehs.assess_tour("CNX-3", blocked_days=200, scanned_days=320,
                        previous={"blocked_days": 3})
    assert v["suspicious"]
    assert "3" in v["reason"] and "200" in v["reason"]


def test_a_steady_high_number_is_not_suspicious_run_after_run():
    """Steady-and-high is normal; it is the JUMP that is the signal."""
    assert not ehs.assess_tour("HKT-3", blocked_days=152, scanned_days=305,
                               previous={"blocked_days": 150})["suspicious"]


def test_a_small_absolute_jump_does_not_cry_wolf():
    """2 -> 10 is more than three times, but only eight days. Both thresholds must be crossed."""
    assert not ehs.assess_tour("CNX-3", blocked_days=10, scanned_days=320,
                               previous={"blocked_days": 2})["suspicious"]


def test_no_previous_run_means_only_the_absolute_guard_applies():
    assert not ehs.assess_tour("CNX-3", blocked_days=100, scanned_days=320, previous=None)["suspicious"]


def test_a_previous_record_without_a_count_is_ignored_not_crashed_on():
    """A tour held back last run deliberately records no blocked_days baseline."""
    assert not ehs.assess_tour("CNX-3", blocked_days=100, scanned_days=320,
                               previous={"status": "suspicious"})["suspicious"]


# ----------------------------------------------------------------------
# describe — what the human is told
# ----------------------------------------------------------------------
def test_never_having_run_is_reported_rather_than_looking_healthy():
    msgs = ehs.describe(None, now=NOW)
    assert msgs and msgs[0]["level"] == "warning"
    assert "never" in msgs[0]["text"].lower()


def test_a_clean_recent_run_says_nothing_at_all():
    """Silence on a good day is what keeps the banner worth reading on a bad one."""
    record = {"finished_utc": (NOW - dt.timedelta(hours=2)).isoformat(),
              "ok": True, "tours": {}, "errors": [], "warnings": []}
    assert ehs.describe(record, now=NOW) == []


def test_a_scheduler_that_stopped_running_is_an_error():
    """The failure with no error message anywhere: the task is simply not running."""
    record = {"finished_utc": (NOW - dt.timedelta(hours=50)).isoformat(),
              "ok": True, "tours": {}, "errors": [], "warnings": []}
    msgs = ehs.describe(record, now=NOW)
    assert any(m["level"] == "error" and "not run" in m["text"] for m in msgs)


def test_a_run_just_inside_the_stale_window_is_not_flagged():
    record = {"finished_utc": (NOW - dt.timedelta(hours=ehs.STALE_AFTER_HOURS - 1)).isoformat(),
              "ok": True, "tours": {}, "errors": [], "warnings": []}
    assert ehs.describe(record, now=NOW) == []


def test_errors_are_reported_as_errors():
    record = {"finished_utc": NOW.isoformat(), "ok": False, "tours": {},
              "errors": ["CNX-3: could not read the supplier's calendar"], "warnings": []}
    msgs = ehs.describe(record, now=NOW)
    assert any(m["level"] == "error" and "CNX-3" in m["text"] for m in msgs)


def test_held_back_readings_are_surfaced_with_how_to_apply_them():
    record = {"finished_utc": NOW.isoformat(), "ok": True, "tours": {},
              "errors": [], "warnings": ["HKT-2: blocked days jumped from 3 to 300"]}
    msgs = ehs.describe(record, now=NOW)
    warning = next(m for m in msgs if m["level"] == "warning")
    assert "HKT-2" in warning["text"]
    assert "force-large" in warning["text"]


def test_a_corrupt_or_missing_timestamp_does_not_crash_the_banner():
    record = {"finished_utc": "not a date", "ok": True, "tours": {}, "errors": [], "warnings": []}
    assert isinstance(ehs.describe(record, now=NOW), list)


# ----------------------------------------------------------------------
# load/save round trip
# ----------------------------------------------------------------------
def test_save_then_load_round_trips(tmp_path):
    ehs.save({"CNX-3": {"status": "ok", "blocked_days": 3}}, "apply", [], [],
             directory=str(tmp_path), now=NOW)
    back = ehs.load(directory=str(tmp_path))
    assert back["ok"] is True
    assert back["mode"] == "apply"
    assert ehs.previous_tour(back, "CNX-3")["blocked_days"] == 3


def test_a_failed_run_is_still_recorded(tmp_path):
    """A failed run that leaves no trace is indistinguishable from one that never happened."""
    ehs.save({}, "apply", ["CNX-3: boom"], [], directory=str(tmp_path), now=NOW)
    back = ehs.load(directory=str(tmp_path))
    assert back["ok"] is False and back["errors"] == ["CNX-3: boom"]


def test_load_returns_none_rather_than_raising_when_there_is_no_file(tmp_path):
    assert ehs.load(directory=str(tmp_path)) is None


def test_load_survives_a_corrupt_status_file(tmp_path):
    (tmp_path / ehs.STATUS_FILENAME).write_text("{not json", encoding="utf-8")
    assert ehs.load(directory=str(tmp_path)) is None
