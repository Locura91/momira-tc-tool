"""
eh_run_status.py — makes a scheduled Elephant Hills stop-sale run visible to a human.

THE PROBLEM THIS EXISTS FOR. Once run_eh_stop_sales.py moves into Windows Task Scheduler it runs
with no window and nobody watching. Every way it can fail is silent and looks exactly like
success from the outside:

  * the supplier redesigns their booking page and the calendar can no longer be read
  * the Travel Compositor login expires, or a tour code changes, and every write is rejected
  * the scheduled task itself stops running - the machine was off, Python moved, the task was
    disabled by an update

In all three the shop keeps selling dates the camp has actually closed, and the only signal is
the absence of a signal. So every run writes a status record, and the Momira app reads it and
says something loudly on screen. The app is opened most days anyway, which is what makes this
the cheapest place to put the warning.

TWO KINDS OF ALARM, and the second one matters more than it looks:

  1. The run FAILED. Easy: something raised, or a write was rejected. Reported as an error.

  2. The run SUCCEEDED but the answer looks wrong. A parser that breaks does not usually throw -
     it quietly reads every day as closed, and a successful-looking run then blocks the entire
     year across every tour. That is the single most expensive thing this tool can do, it is
     completely silent, and no amount of exception handling catches it, because nothing went
     wrong in the code's own terms. So a run that is about to block an unusual amount is treated
     as suspicious, NOT written, and reported for a human to confirm.

WHY "UNUSUAL" IS MEASURED TWO WAYS. A fixed threshold cannot work here: HKT-3 legitimately
blocks something like half its window, while CNX-3 blocks three days. So:

  * ABSOLUTE guard - at or above NEARLY_EVERYTHING_CLOSED of the scanned days, something is
    almost certainly broken rather than genuinely sold out. A camp that is shut for an entire
    year would be told to us, not discovered by a scraper.
  * RELATIVE guard - a sudden jump against what the same tour blocked on the previous run.
    Steady-and-high is normal (HKT-3); a tour that went from 3 blocked days to 300 overnight is
    not. This is why the status file keeps per-tour counts: they are the baseline for next time.

A suspicious tour is skipped, not applied, and the operator confirms with --force-large. The
asymmetry is deliberate: a missed stop sale costs one oversold booking that a human can fix,
while a wrongly-applied mass block silently removes a year of sellable inventory and nobody
notices, because a product that stops appearing looks exactly like a product nobody searched for.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Any, Dict, List, Optional

TOOL_VERSION = "2026-10-07-eh-website-stop-sale-reader"

STATUS_FILENAME = "eh_stop_sales_status.json"

# At or above this share of the scanned window reading as closed, assume the reader is broken
# rather than the camp being shut for a year.
NEARLY_EVERYTHING_CLOSED = 0.95

# A jump beyond BOTH of these against the previous run is suspicious. Both, not either, so a tour
# going from 2 blocked days to 10 does not cry wolf.
SUSPICIOUS_JUMP_MULTIPLE = 3.0
SUSPICIOUS_JUMP_MINIMUM = 45

# How long before a silent scheduler counts as a problem. A daily task that has not reported in
# 36 hours has missed at least one run.
STALE_AFTER_HOURS = 36


def status_path(directory: Optional[str] = None) -> str:
    base = directory or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, STATUS_FILENAME)


# ======================================================================
# Deciding whether a reading looks wrong
# ======================================================================
def assess_tour(tour_code: str, blocked_days: int, scanned_days: int,
                previous: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Is this tour's reading plausible, or does it look like a broken parser?

    Returns {"suspicious": bool, "reason": str or None}. Pure - no files, no clock - so the
    thresholds can be tested directly."""
    if scanned_days <= 0:
        return {"suspicious": True,
                "reason": "the scan window came back empty, so nothing could be checked"}

    fraction = blocked_days / scanned_days
    if fraction >= NEARLY_EVERYTHING_CLOSED:
        return {"suspicious": True,
                "reason": (f"{blocked_days} of {scanned_days} scanned days read as closed "
                           f"({fraction:.0%}). A camp closed for essentially a whole year would "
                           f"have told us directly - this is far more likely to be the booking "
                           f"page having changed shape so every day reads as FULL")}

    if previous:
        before = previous.get("blocked_days")
        if isinstance(before, int) and before >= 0:
            if (blocked_days > before * SUSPICIOUS_JUMP_MULTIPLE
                    and blocked_days - before >= SUSPICIOUS_JUMP_MINIMUM):
                return {"suspicious": True,
                        "reason": (f"blocked days jumped from {before} on the previous run to "
                                   f"{blocked_days} now. That may be a real mass closure, but it "
                                   f"is the shape a broken reader also makes, so it needs a human "
                                   f"to confirm before it is written")}

    return {"suspicious": False, "reason": None}


# ======================================================================
# Reading and writing the record
# ======================================================================
def load(directory: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The previous run's record, or None. Never raises: a missing or corrupt status file must
    not stop a run or break the app's banner."""
    try:
        with open(status_path(directory), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def previous_tour(record: Optional[Dict[str, Any]], tour_code: str) -> Optional[Dict[str, Any]]:
    if not record:
        return None
    tours = record.get("tours")
    if not isinstance(tours, dict):
        return None
    entry = tours.get(tour_code)
    return entry if isinstance(entry, dict) else None


def save(tours: Dict[str, Any], mode: str, errors: List[str], warnings: List[str],
         directory: Optional[str] = None, now: Optional[_dt.datetime] = None,
         previous_finished_utc: Optional[str] = None) -> Dict[str, Any]:
    """Record this run. Written even when the run failed - especially then, since a failed run
    that leaves no trace is indistinguishable from a run that never happened.

    previous_finished_utc (2026-10-08): the timestamp of the run this one is being compared
    against, kept on the record so the app's "what changed since yesterday" button can say WHICH
    earlier run the per-tour `changed` diffs are measured from, without needing a second file."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    record = {
        "tool_version": TOOL_VERSION,
        "finished_utc": now.replace(microsecond=0).isoformat(),
        "previous_finished_utc": previous_finished_utc,
        "mode": mode,
        "ok": not errors,
        "tours": tours,
        "errors": errors,
        "warnings": warnings,
    }
    try:
        with open(status_path(directory), "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2, ensure_ascii=False)
    except Exception:
        pass  # best effort: never fail a successful run over its own bookkeeping

    # CONFIRMED PRODUCT-OWNER REQUEST (2026-10-08, step 1): ALSO record the run in the shared
    # platform database, so the status is visible everywhere the app runs - above all the deployed
    # (Streamlit Cloud) app, which never sees the local file because the reader runs on the office
    # PC. Only durable when DATABASE_URL points the reader at the same database the app uses; if it
    # does not (or psycopg2 is missing), platform_store falls back to its local store and the
    # deployed app simply won't see it - so this is additive, never a replacement for the file, and
    # a DB failure never affects the run.
    _write_status_to_db(record)
    return record


DB_NAMESPACE = "eh_stop_sales"
DB_KEY = "status"


def _write_status_to_db(record: Dict[str, Any]) -> bool:
    """Best-effort upsert of the latest run into platform_store. Never raises."""
    try:
        import platform_store
        return platform_store.set(DB_NAMESPACE, DB_KEY, record)
    except Exception:
        return False


def load_from_db() -> Optional[Dict[str, Any]]:
    """The latest run as recorded in the shared database, or None. Used (step 2) by the app so the
    automatic view works on the deployed site, not only on the machine that holds the local file."""
    try:
        import platform_store
        value = platform_store.get(DB_NAMESPACE, DB_KEY)
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def load_for_display(directory: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The record to SHOW in the app (step 2). The shared database comes FIRST, so the deployed
    site and every machine see the same thing wherever the reader actually ran; the local file is
    only a fallback for a machine that has the file but no database. This is what the banner and the
    automatic-status view read - never the bare local file - so the reader's result reaches the app
    no matter which PC produced it."""
    record = load_from_db()
    if record is not None:
        return record
    return load(directory)


# ======================================================================
# What the app should say
# ======================================================================
def describe(record: Optional[Dict[str, Any]], now: Optional[_dt.datetime] = None) -> List[Dict[str, str]]:
    """Turn a status record into the messages a human should see. Pure, so it is testable without
    Streamlit. Each entry is {"level": "error"|"warning"|"info", "text": ...}."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    out: List[Dict[str, str]] = []

    if record is None:
        # CONFIRMED PRODUCT-OWNER REQUEST (2026-10-07): say NOTHING when there is no status file.
        # This used to warn "has never reported a run", which read as a permanent fault on the
        # DEPLOYED app: the reader is a scheduled task on the office machine and writes its status
        # file there (and that file is git-ignored), so it is never present on Streamlit Cloud and
        # the warning could never be satisfied. "No status file" means "the reader does not run on
        # this host" - the normal case, not something to report. A reader that IS set up and then
        # goes quiet is still caught, by the STALE_AFTER_HOURS check below, which needs a previous
        # run to measure against anyway.
        return []

    finished = _parse_iso(record.get("finished_utc"))
    if finished is not None:
        age_h = (now - finished).total_seconds() / 3600.0
        if age_h > STALE_AFTER_HOURS:
            out.append({"level": "error",
                        "text": (f"**Elephant Hills stop-sale check has not run for "
                                 f"{int(age_h)} hours.** A daily task that goes quiet usually "
                                 f"means the machine was off or the scheduled task is disabled. "
                                 f"Until it runs, new closures on the supplier's website are not "
                                 f"reaching Travel Compositor.")})

    errors = [e for e in (record.get("errors") or []) if e]
    if errors:
        out.append({"level": "error",
                    "text": ("**The last Elephant Hills stop-sale check failed.** Closures found "
                             "on the supplier's website are not reaching Travel Compositor:\n\n"
                             + "\n".join(f"- {e}" for e in errors))})

    warnings = [w for w in (record.get("warnings") or []) if w]
    if warnings:
        out.append({"level": "warning",
                    "text": ("**The last Elephant Hills stop-sale check needs a human to look at "
                             "it.** It read something unusual and did NOT write it:\n\n"
                             + "\n".join(f"- {w}" for w in warnings)
                             + "\n\nIf the reading is correct, re-run with `--force-large` to "
                               "apply it.")})
    return out


def render_banner(st, directory: Optional[str] = None) -> None:
    """Show the messages in Streamlit. Wrapped so a problem here can never take the app down -
    a broken warning system must not become a worse outage than the thing it warns about."""
    try:
        for msg in describe(load_for_display(directory)):
            if msg["level"] == "error":
                st.error("🚨 " + msg["text"])
            elif msg["level"] == "warning":
                st.warning("⚠️ " + msg["text"])
            else:
                st.info(msg["text"])
    except Exception:
        pass


def _parse_iso(value: Any) -> Optional[_dt.datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = _dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_dt.timezone.utc)


# ======================================================================
# "What changed since yesterday" - a day-over-day diff of the blocked dates
# ======================================================================
# CONFIRMED PRODUCT-OWNER REQUEST (2026-10-08): a small button at the bottom of the app showing
# today's stop-sale status and "a short list what changed since yesterday". The status file used
# to keep only a per-tour COUNT; to say which DATES newly closed (or reopened) we now also store
# each tour's blocked ranges and the diff against the previous run. These helpers are pure (no
# files, no clock unless passed one) so the diff and the wording are both testable directly.
_ONE_DAY = _dt.timedelta(days=1)
_MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _ranges_to_days(ranges: Optional[List[Dict[str, str]]]) -> set:
    """Every individual date covered by a list of {start,end} ISO ranges. A malformed range is
    skipped rather than crashing the whole diff - the status file is best-effort."""
    days: set = set()
    for r in ranges or []:
        try:
            start = _dt.date.fromisoformat(r["start"])
            end = _dt.date.fromisoformat(r["end"])
        except (KeyError, TypeError, ValueError):
            continue
        day = start
        while day <= end:
            days.add(day)
            day += _ONE_DAY
    return days


def _days_to_ranges(days: set) -> List[Dict[str, str]]:
    """Collapse a set of dates back into consecutive {start,end} ranges, in date order."""
    out: List[Dict[str, str]] = []
    for day in sorted(days):
        if out and day == _dt.date.fromisoformat(out[-1]["end"]) + _ONE_DAY:
            out[-1]["end"] = day.isoformat()
        else:
            out.append({"start": day.isoformat(), "end": day.isoformat()})
    return out


def diff_ranges(previous_ranges: Optional[List[Dict[str, str]]],
                current_ranges: Optional[List[Dict[str, str]]]) -> Dict[str, List[Dict[str, str]]]:
    """What is newly closed (added) and newly reopened (removed) between two runs' blocked dates.

    Reopened dates are reported for the human to see, never acted on - the reader only ever ADDS
    stop sales, it never removes one (that is the whole merge-never-replace safety rule), so a
    date leaving the supplier's closed list is information, not an instruction to unblock."""
    prev = _ranges_to_days(previous_ranges)
    curr = _ranges_to_days(current_ranges)
    return {"added": _days_to_ranges(curr - prev), "removed": _days_to_ranges(prev - curr)}


def _fmt_date(iso: str) -> str:
    try:
        d = _dt.date.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso
    return f"{d.day} {_MONTHS[d.month]} {d.year}"


def _fmt_ranges(ranges: List[Dict[str, str]], limit: int = 6) -> str:
    """Compact human list: "14 Jan 2027, 2–5 Feb 2027". Long lists are truncated with a count so
    the "short list" the product owner asked for never becomes a wall of dates."""
    parts = []
    for r in ranges:
        if r["start"] == r["end"]:
            parts.append(_fmt_date(r["start"]))
        else:
            parts.append(f"{_fmt_date(r['start'])} – {_fmt_date(r['end'])}")
    if len(parts) > limit:
        extra = len(parts) - limit
        parts = parts[:limit] + [f"+{extra} more"]
    return ", ".join(parts)


def _relative_when(finished: Optional[_dt.datetime], now: _dt.datetime) -> str:
    if finished is None:
        return "at an unknown time"
    hhmm = finished.strftime("%H:%M UTC")
    days_ago = (now.date() - finished.date()).days
    if days_ago <= 0:
        return f"today at {hhmm}"
    if days_ago == 1:
        return f"yesterday at {hhmm}"
    return f"{days_ago} days ago ({finished.date().isoformat()} {hhmm})"


def today_lines(record: Optional[Dict[str, Any]],
                now: Optional[_dt.datetime] = None) -> List[Dict[str, str]]:
    """The content of the bottom "today's status" button, as {level, text} entries. Pure.

    Unlike describe() - which is deliberately SILENT while everything is fine, because it drives
    the always-on banner - this is shown only when the human clicks the button open, so it always
    says something: when the last run was, whether it was clean, and the short per-tour list of
    what changed since the previous run."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    if record is None:
        return [{"level": "info",
                 "text": ("No Elephant Hills stop-sale run has been recorded on this machine yet. "
                          "The daily check runs on the office PC; this is normal everywhere else.")}]

    out: List[Dict[str, str]] = []
    finished = _parse_iso(record.get("finished_utc"))
    when = _relative_when(finished, now)
    ran_today = finished is not None and finished.date() >= now.date()

    if record.get("errors"):
        out.append({"level": "error", "text": f"Last run ({when}) **failed** — see the alert above."})
    elif not ran_today:
        out.append({"level": "warning",
                    "text": (f"Last completed run was **{when}** — today's run has not completed "
                             f"yet (the PC may have been off at the scheduled time).")})
    else:
        out.append({"level": "success", "text": f"Ran **{when}**. All tours checked."})

    prev_when = _parse_iso(record.get("previous_finished_utc"))
    since = f" since {_relative_when(prev_when, now)}" if prev_when else " since the previous run"

    tours = record.get("tours") or {}
    for code in sorted(tours):
        entry = tours[code] if isinstance(tours.get(code), dict) else {}
        status = entry.get("status")
        if status == "failed":
            out.append({"level": "error", "text": f"**{code}**: could not be read this run."})
            continue
        if status == "suspicious":
            out.append({"level": "warning",
                        "text": f"**{code}**: an unusually large reading was held back for review."})
            continue
        blocked = entry.get("blocked_days")
        changed = entry.get("changed") or {}
        added, removed = changed.get("added") or [], changed.get("removed") or []
        bits = []
        if added:
            bits.append(f"🔒 {sum_days(added)} newly closed{since}: {_fmt_ranges(added)}")
        if removed:
            bits.append(f"🔓 {sum_days(removed)} reopened on the supplier (not unblocked): {_fmt_ranges(removed)}")
        if bits:
            tail = " — " + "; ".join(bits)
        elif "changed" in entry:
            tail = " — no change" + since
        else:
            # An older status file written before day-over-day diffs existed: we have the count
            # but nothing to compare against, so don't claim "no change".
            tail = ""
        out.append({"level": "info", "text": f"**{code}**: {blocked} day(s) blocked{tail}"})
    return out


def sum_days(ranges: List[Dict[str, str]]) -> int:
    return len(_ranges_to_days(ranges))


def render_today_status(st, directory: Optional[str] = None) -> None:
    """Today's automatic stop-sale status, rendered inline - when it last ran, each tour's blocked
    count, and the short list of what changed since the previous run.

    CONFIRMED PRODUCT-OWNER REQUEST (2026-10-08): this lives INSIDE the Stop Sales tool now (one of
    its two modes: add from an email, or see the automatic checks), not as a separate button on the
    main page - "it's all stop sales, so it should all go to the stop sales". Wrapped so a problem
    here can never take the hosting screen down."""
    try:
        for msg in today_lines(load_for_display(directory)):
            level = msg["level"]
            if level == "error":
                st.error(msg["text"])
            elif level == "warning":
                st.warning(msg["text"])
            elif level == "success":
                st.success(msg["text"])
            else:
                st.markdown("- " + msg["text"])
    except Exception:
        pass
