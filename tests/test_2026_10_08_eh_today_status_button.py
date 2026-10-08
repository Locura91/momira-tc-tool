"""
Tests for the bottom "today's Elephant Hills stop-sale status" button (product-owner request,
2026-10-08): "one small button on the bottom for the todays stop sale status and a short list what
changed since yesterday."

The diff (diff_ranges) and the wording (today_lines) are pure, so both are tested directly without
Streamlit. The inline renderer (render_today_status), shown inside the Stop Sales tool, is a thin
guarded wrapper over today_lines.
"""
import datetime as dt
import os

import eh_run_status as ehs

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 8, 9, 0, tzinfo=UTC)


def _r(start, end=None):
    return {"start": start, "end": end or start}


# ----------------------------------------------------------------------
# diff_ranges — what changed between two runs
# ----------------------------------------------------------------------
def test_newly_closed_dates_are_reported_as_added():
    d = ehs.diff_ranges([_r("2027-01-01", "2027-01-03")],
                        [_r("2027-01-01", "2027-01-05")])
    assert d["added"] == [_r("2027-01-04", "2027-01-05")]
    assert d["removed"] == []


def test_reopened_dates_are_reported_as_removed():
    d = ehs.diff_ranges([_r("2027-01-01", "2027-01-05")],
                        [_r("2027-01-01", "2027-01-03")])
    assert d["removed"] == [_r("2027-01-04", "2027-01-05")]
    assert d["added"] == []


def test_no_change_is_empty_both_ways():
    same = [_r("2027-02-01", "2027-02-10")]
    assert ehs.diff_ranges(same, same) == {"added": [], "removed": []}


def test_first_ever_run_has_no_previous_so_everything_is_added():
    d = ehs.diff_ranges(None, [_r("2027-03-01")])
    assert d["added"] == [_r("2027-03-01")]
    assert d["removed"] == []


def test_scattered_new_days_collapse_back_into_tidy_ranges():
    d = ehs.diff_ranges([], [_r("2027-01-01"), _r("2027-01-02"), _r("2027-01-03"), _r("2027-01-10")])
    assert d["added"] == [_r("2027-01-01", "2027-01-03"), _r("2027-01-10")]


def test_a_malformed_range_is_skipped_not_crashed_on():
    d = ehs.diff_ranges([{"start": "oops"}], [_r("2027-01-01")])
    assert d["added"] == [_r("2027-01-01")]


def test_sum_days_counts_individual_dates():
    assert ehs.sum_days([_r("2027-01-01", "2027-01-03"), _r("2027-01-10")]) == 4


# ----------------------------------------------------------------------
# today_lines — the content the button shows
# ----------------------------------------------------------------------
def _record(**over):
    base = {"finished_utc": (NOW - dt.timedelta(hours=3)).isoformat(),
            "previous_finished_utc": (NOW - dt.timedelta(days=1)).isoformat(),
            "ok": True, "errors": [], "warnings": [], "tours": {}}
    base.update(over)
    return base


def test_no_status_file_says_so_plainly_without_alarm():
    msgs = ehs.today_lines(None, now=NOW)
    assert len(msgs) == 1 and msgs[0]["level"] == "info"
    assert "no" in msgs[0]["text"].lower()


def test_a_run_from_today_reports_success_at_the_top():
    msgs = ehs.today_lines(_record(), now=NOW)
    assert msgs[0]["level"] == "success"
    assert "today" in msgs[0]["text"].lower()


def test_a_run_only_from_yesterday_is_flagged_as_todays_run_missing():
    rec = _record(finished_utc=(NOW - dt.timedelta(days=1)).isoformat())
    msgs = ehs.today_lines(rec, now=NOW)
    assert msgs[0]["level"] == "warning"
    assert "not completed" in msgs[0]["text"].lower()


def test_a_failed_run_is_surfaced_as_an_error():
    msgs = ehs.today_lines(_record(errors=["CNX-3: boom"]), now=NOW)
    assert msgs[0]["level"] == "error"


def test_per_tour_change_lists_the_newly_closed_dates():
    rec = _record(tours={"HKT-2": {"status": "ok", "blocked_days": 88,
                                   "changed": {"added": [_r("2027-01-14", "2027-01-15")],
                                               "removed": []}}})
    line = next(m["text"] for m in ehs.today_lines(rec, now=NOW) if "HKT-2" in m["text"])
    assert "88 day" in line
    assert "newly closed" in line
    assert "14 Jan 2027" in line and "15 Jan 2027" in line


def test_per_tour_no_change_says_no_change_when_a_diff_was_computed():
    rec = _record(tours={"CNX-3": {"status": "ok", "blocked_days": 3,
                                   "changed": {"added": [], "removed": []}}})
    line = next(m["text"] for m in ehs.today_lines(rec, now=NOW) if "CNX-3" in m["text"])
    assert "no change" in line.lower()


def test_reopened_dates_are_shown_but_described_as_not_unblocked():
    rec = _record(tours={"HKT-3": {"status": "ok", "blocked_days": 100,
                                   "changed": {"added": [], "removed": [_r("2027-02-01")]}}})
    line = next(m["text"] for m in ehs.today_lines(rec, now=NOW) if "HKT-3" in m["text"])
    assert "reopened" in line.lower()
    assert "not unblocked" in line.lower()


def test_an_old_record_without_a_diff_does_not_claim_no_change():
    rec = _record(previous_finished_utc=None,
                  tours={"CNX-4": {"status": "ok", "blocked_days": 3}})  # no "changed" key
    line = next(m["text"] for m in ehs.today_lines(rec, now=NOW) if "CNX-4" in m["text"])
    assert "no change" not in line.lower()
    assert "3 day" in line


def test_a_held_back_tour_is_shown_as_needing_review():
    rec = _record(tours={"HKT-2": {"status": "suspicious", "detail": "jumped"}})
    line = next(m["text"] for m in ehs.today_lines(rec, now=NOW) if "HKT-2" in m["text"])
    assert "held back" in line.lower() or "review" in line.lower()


# ----------------------------------------------------------------------
# wiring + save round-trip
# ----------------------------------------------------------------------
def test_save_records_the_previous_runs_timestamp(tmp_path):
    ehs.save({"CNX-3": {"status": "ok", "blocked_days": 3}}, "apply", [], [],
             directory=str(tmp_path), now=NOW, previous_finished_utc="2026-10-07T06:30:00+00:00")
    back = ehs.load(directory=str(tmp_path))
    assert back["previous_finished_utc"] == "2026-10-07T06:30:00+00:00"


def test_the_status_view_lives_inside_the_stop_sales_tool():
    # CONFIRMED PRODUCT-OWNER REQUEST (2026-10-08): the on-demand status moved OFF the main page
    # and INTO the Stop Sales tool as one of its two modes.
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(repo, "stop_sales_tool.py"), "r", encoding="utf-8") as f:
        sst = f.read()
    assert "eh_run_status.render_today_status(st)" in sst
    assert "ss_mode" in sst  # the add-email / automatic-reader choice
    with open(os.path.join(repo, "app.py"), "r", encoding="utf-8") as f:
        app = f.read()
    # the main page no longer carries its own today's-status button, only the silent failure alert
    assert "render_today_button" not in app
    assert "render_today_status" not in app
    assert "_eh_run_status.render_banner(st)" in app


def test_runner_stores_ranges_and_the_diff():
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(repo, "run_eh_stop_sales.py"), "r", encoding="utf-8") as f:
        runner = f.read()
    assert '"ranges": reading["ranges"]' in runner
    assert "eh_run_status.diff_ranges(" in runner
    assert 'previous_finished_utc=(previous or {}).get("finished_utc")' in runner
