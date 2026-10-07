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
         directory: Optional[str] = None, now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    """Record this run. Written even when the run failed - especially then, since a failed run
    that leaves no trace is indistinguishable from a run that never happened."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    record = {
        "tool_version": TOOL_VERSION,
        "finished_utc": now.replace(microsecond=0).isoformat(),
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
    return record


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
        for msg in describe(load(directory)):
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
