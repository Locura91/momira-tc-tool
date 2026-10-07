"""
eh_availability.py — read Elephant Hills / Bush Camp stop sales straight off the supplier's
own booking calendar on ehtms.com, instead of waiting for a stop-sale email.

WHY THIS EXISTS: the email reader (stop_sales_parser.py + stop_sales_tool.py) only ever learns
about a closure once a human at the supplier writes one up and sends it. Elephant Hills does not
reliably do that — their booking engine is the source of truth, and a date silently goes FULL
there with no email at all. Every day between a date closing on ehtms.com and that block reaching
Travel Compositor is a day a customer can book a tour that cannot be delivered.

WHAT IT READS. The ehtms.com check-in page renders a month calendar of day buttons. Verified
against the live site on 2026-10-06 across a full 14-month walk, with zero disagreements between
the two independent signals the page exposes:

  * a CLOSED day  -> <button disabled> whose FULL badge span is visible (class has text-danger)
  * an OPEN day   -> <button> not disabled, whose FULL badge span carries class "invisible"

Every day button renders the FULL badge; on a sellable day it is merely hidden by CSS. So the
badge's *text* is useless on its own — "FULL" is in the DOM for every single day of every month.
`button.disabled` is the signal this module trusts, and `badge_visible` is read too and compared,
so that a redesign which breaks one of the two is reported as a disagreement rather than silently
misread. A disagreement means the page changed shape and a human must look: the scraper raises
rather than guessing, because guessing wrong in the "open" direction sells undeliverable tours and
guessing wrong in the "closed" direction silently destroys sellable inventory.

THE CALENDAR'S OWN LIMIT: ehtms.com renders exactly 12 months forward and then disables its
next-month arrow. That is the same 365-day horizon the product owner specified, enforced by the
site itself, so the walk stops naturally rather than needing a page count.

THE RELEASE WINDOW, AND WHY THE NEAR TERM IS DELIBERATELY IGNORED (CONFIRMED RULE, product owner,
2026-10-07): the first several weeks of the calendar always read FULL, because those dates sit
inside the supplier's release period — allotment has not been released to us yet, which is NOT the
same thing as the tour being closed. Writing those to Travel Compositor would block dates that are
merely unreleased.

The release period is a contractual lead time, not anything the supplier's website exposes, and it
differs per tour — so it is configured per tour and the scan starts the day after it ends:

    CNX-3, CNX-4 (The Bush Camp)  release 45 days -> scan day 46..365
    HKT-2, HKT-3 (Elephant Hills) release 60 days -> scan day 61..365

  A genuine closure inside the release period is NOT written, and that is correct rather than a
  gap: those dates were never ours to sell. The CNX tours' real 2026-11-07..09 block falls on day
  31..33, inside their 45-day release, so it stays unwritten even at a 46-day start. Everything
  skipped is still scraped and returned in `skipped_near_term`, so the run log shows a human what
  the window hid rather than hiding it twice over.

THIS MODULE ONLY READS. It does not touch Travel Compositor and it never submits anything to
ehtms.com — no date is clicked, no pax or room count is changed, the booking form is never
advanced past the calendar. The only control it operates is the month-forward arrow.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, Iterable, List, Optional, Sequence

# Stamped on every delivery, matching the convention in stop_sales_tool.py and friends: app.py
# compares this against its own build string so a partial push surfaces as a clear message rather
# than a traceback pointing at unrelated code.
MODULE_BUILD = "2026-10-07-eh-website-stop-sale-reader"

# CONFIRMED RULE (product owner, 2026-10-07): the scan starts the day AFTER the tour's release
# period ends and runs to day 365, inclusive. Day 1 is tomorrow.
#
# The release period is OUR contractual lead time with the supplier, not anything visible on
# their website - inside it the dates are not ours to sell, so a FULL day there is not a closure
# we should write. It differs per tour, which is why it lives in the config per tour rather than
# as one global number:
#     CNX-3, CNX-4 (The Bush Camp)  -> 45 days, so the scan starts at day 46
#     HKT-2, HKT-3 (Elephant Hills) -> 60 days, so the scan starts at day 61
#
# This is why the real 2026-11-07..09 block on the CNX tours is still correctly skipped even at a
# 46-day start: it falls on day 31-33, inside the 45-day release, so it is not inventory we could
# have sold anyway.
DEFAULT_RELEASE_DAYS = 60
SCAN_TO_DAY = 365

# Kept as the module-level default for callers that do not pass a release length. Equal to
# DEFAULT_RELEASE_DAYS + 1, since day 1 is tomorrow and the first sellable day is the one after
# the release period ends.
SCAN_FROM_DAY = DEFAULT_RELEASE_DAYS + 1


def first_scan_day(release_days: Optional[int] = None) -> int:
    """The first day number this run may write, given a tour's release length.

    A 45-day release means days 1..45 are unsellable, so day 46 is the first real candidate."""
    if release_days is None:
        return SCAN_FROM_DAY
    try:
        days = int(release_days)
    except (TypeError, ValueError):
        raise ValueError(f"release_days must be a whole number of days, got {release_days!r}")
    if days < 0:
        raise ValueError(f"release_days cannot be negative, got {days}")
    return days + 1

# The supplier every tour read by this module belongs to.
SUPPLIER_ID = "MOMIRA_TH_EH"

_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}


class CalendarShapeChanged(RuntimeError):
    """The ehtms.com calendar no longer matches what this module knows how to read.

    Raised rather than falling back to a best guess. Both failure directions are silent and
    expensive — see the module docstring — so a shape change must reach a human, not be papered
    over with whichever signal still happens to parse."""


# ======================================================================
# Pure date logic — no browser, no network, fully testable
# ======================================================================
def parse_month_label(label: str) -> tuple:
    """'Oct 2026' -> (2026, 10). The calendar's own header is the only month source."""
    parts = (label or "").strip().split()
    if len(parts) != 2 or parts[0] not in _MONTHS:
        raise CalendarShapeChanged(
            f"month header {label!r} is not the expected 'Mon YYYY' shape")
    try:
        year = int(parts[1])
    except ValueError:
        raise CalendarShapeChanged(f"month header {label!r} has a non-numeric year")
    return year, _MONTHS[parts[0]]


def scan_window(today: Optional[_dt.date] = None,
                from_day: int = SCAN_FROM_DAY,
                to_day: int = SCAN_TO_DAY) -> tuple:
    """The (first, last) dates this run is allowed to write, inclusive.

    Day 1 is tomorrow, so day 61 is today + 61 days."""
    today = today or _dt.date.today()
    return today + _dt.timedelta(days=from_day), today + _dt.timedelta(days=to_day)


def days_to_ranges(days: Iterable[_dt.date]) -> List[Dict[str, str]]:
    """Collapse individual closed days into inclusive contiguous ranges.

    Travel Compositor stores a stop sale as a {start, end} range, and three consecutive closed
    days are one range there, not three. Merging here keeps the live record readable and keeps
    merge_stop_sales' duplicate detection meaningful across runs — a day-at-a-time write would
    produce a different set of entries every time the supplier extends a closure by one day."""
    ordered = sorted(set(d for d in days if d is not None))
    out: List[Dict[str, str]] = []
    for day in ordered:
        if out and _dt.date.fromisoformat(out[-1]["end"]) + _dt.timedelta(days=1) == day:
            out[-1]["end"] = day.isoformat()
        else:
            out.append({"start": day.isoformat(), "end": day.isoformat()})
    return out


def split_by_window(closed_days: Iterable[_dt.date],
                    today: Optional[_dt.date] = None,
                    from_day: int = SCAN_FROM_DAY,
                    to_day: int = SCAN_TO_DAY) -> Dict[str, List[_dt.date]]:
    """Separate the closed days into the ones this run may write and the ones it must not.

    Three buckets, all returned, because the two it will not write are the ones a human needs to
    be able to see in the log:
      in_window      - day from_day..to_day, inclusive: these become stop sales
      skipped_near_term - today..day from_day-1: inside the release period, deliberately ignored
      skipped_past   - before today: the calendar greys out elapsed dates, never a real closure
    """
    today = today or _dt.date.today()
    first, last = scan_window(today, from_day, to_day)
    buckets: Dict[str, List[_dt.date]] = {"in_window": [], "skipped_near_term": [], "skipped_past": []}
    for day in sorted(set(d for d in closed_days if d is not None)):
        if day < today:
            buckets["skipped_past"].append(day)
        elif day < first:
            buckets["skipped_near_term"].append(day)
        elif day <= last:
            buckets["in_window"].append(day)
        # beyond `last` is outside the agreed horizon and simply dropped; the calendar stops at
        # 12 months anyway, so this only ever trims the tail of the final month.
    return buckets


def month_days_to_dates(year: int, month: int, day_numbers: Sequence[int]) -> List[_dt.date]:
    """Turn ('2026', 10, [7, 8, 9]) into real dates, rejecting anything the month cannot hold."""
    out = []
    for n in day_numbers:
        try:
            out.append(_dt.date(year, month, int(n)))
        except (ValueError, TypeError):
            raise CalendarShapeChanged(
                f"calendar offered day {n!r} in {year}-{month:02d}, which is not a real date")
    return out


# ======================================================================
# Reading the live calendar
# ======================================================================
# The DOM contract, verified live 2026-10-06. Kept as named constants so a page redesign shows up
# as one obvious place to re-verify rather than as selectors scattered through the walk.
DAY_BUTTON_SELECTOR = 'button[data-slot="button"]'
MONTH_LABEL_PATTERN = r"^[A-Z][a-z]{2} 20\d\d$"

# Runs inside the page. Returns the month header plus, for every day button, the day number and
# BOTH independent closed/open signals, so the caller can insist they agree.
_READ_MONTH_JS = r"""
() => {
  const labelEl = [...document.querySelectorAll('*')].find(
    e => e.children.length === 0 && /^[A-Z][a-z]{2} 20\d\d$/.test((e.textContent || '').trim()));
  const days = [...document.querySelectorAll('button[data-slot="button"]')].map(b => {
    const spans = [...b.querySelectorAll('span')];
    if (!spans.length) return null;
    const n = (spans[0].textContent || '').trim();
    if (!/^\d{1,2}$/.test(n)) return null;
    const badge = spans[1] || null;
    const badgeVisible = !!badge && !/\binvisible\b/.test((badge.className || '').toString());
    return { day: parseInt(n, 10), disabled: b.disabled === true, badgeVisible };
  }).filter(Boolean);
  return { label: labelEl ? labelEl.textContent.trim() : null, days };
}
"""


def interpret_month(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Turn one month's raw DOM reading into closed dates, insisting the two signals agree.

    `disabled` and the visible FULL badge are rendered independently by the page. Across the
    14-month verification walk they never disagreed. If they ever do, the page has changed shape
    and this refuses to pick a winner — see CalendarShapeChanged."""
    year, month = parse_month_label(payload.get("label") or "")
    days = payload.get("days") or []
    if not days:
        raise CalendarShapeChanged(f"{payload.get('label')!r} rendered no day buttons at all")
    disagreements = [d["day"] for d in days if bool(d.get("disabled")) != bool(d.get("badgeVisible"))]
    if disagreements:
        raise CalendarShapeChanged(
            f"in {payload.get('label')}, the disabled flag and the FULL badge disagree on "
            f"day(s) {disagreements} - the calendar's markup has changed and must be re-verified "
            f"by a human before any stop sale is trusted from it")
    closed = [d["day"] for d in days if d.get("disabled")]
    return {"year": year, "month": month,
            "closed_dates": month_days_to_dates(year, month, closed),
            "total_days": len(days)}


def read_calendar(page, max_months: int = 14) -> List[Dict[str, Any]]:
    """Walk the open calendar forward, reading each month. Only the forward arrow is clicked.

    `page` is a Playwright page with the calendar dialog already open. Stops when the month stops
    advancing, which is how ehtms.com signals its 12-month horizon (the arrow goes inert rather
    than disappearing)."""
    months: List[Dict[str, Any]] = []
    seen_labels = set()
    for _ in range(max_months):
        payload = page.evaluate(_READ_MONTH_JS)
        label = (payload or {}).get("label")
        if not label or label in seen_labels:
            break  # the arrow stopped advancing: we are at the end of the supplier's horizon
        seen_labels.add(label)
        months.append(interpret_month(payload))
        if not _click_next_month(page):
            break
    if not months:
        raise CalendarShapeChanged("the calendar never rendered a readable month")
    return months


def _click_next_month(page) -> bool:
    """Advance one month. Returns False when there is no usable forward arrow left.

    The arrows are the only text-less visible buttons in the dialog; the last of them is the
    forward one. Deliberately narrow: nothing here may click a day cell or the submit button."""
    try:
        return bool(page.evaluate("""
        () => {
          const navs = [...document.querySelectorAll('button')]
            .filter(b => !(b.textContent || '').trim() && b.offsetParent);
          if (!navs.length) return false;
          const fwd = navs[navs.length - 1];
          if (fwd.disabled) return false;
          fwd.click();
          return true;
        }
        """))
    except Exception:
        return False


def closed_dates_for_url(page, url: str, open_calendar_timeout_ms: int = 15000) -> List[_dt.date]:
    """Every closed date the supplier's calendar shows for one tour URL, across its whole horizon.

    Opens the page, advances to the check-in step, opens the calendar, walks it, and returns the
    closed dates unfiltered — the window filter is applied later by split_by_window, so the run log
    can report what was seen as well as what was written. Nothing is booked, selected or submitted:
    no date is clicked, the room count is left alone, and the funnel is never advanced past the
    calendar step.
    """
    page.goto(url, wait_until="domcontentloaded")
    _advance_to_checkin(page, open_calendar_timeout_ms)
    _open_calendar(page, open_calendar_timeout_ms)
    months = read_calendar(page)
    out: List[_dt.date] = []
    for m in months:
        out.extend(m["closed_dates"])
    return out


# The booking funnel's step-1 page (the package description, with its rate table) sits at "/" and
# the calendar at /booking/checkin. Some tour URLs point straight at the calendar and some at
# step 1, and a /booking/checkin URL for a package the session has not "entered" yet redirects
# back to step 1 - verified live on 2026-10-07 with package_id=10. So rather than trusting the
# configured URL's path, this always looks at where it actually landed and clicks Continue if it
# is still on step 1. Continue only moves between wizard steps; it submits no data.
CONTINUE_BUTTON_TEXT = "Continue"
CHECKIN_FIELD_TEXT = "Select check in date"


def _advance_to_checkin(page, timeout_ms: int) -> None:
    """If we landed on the package description step, click Continue to reach the calendar step."""
    if _checkin_field(page).count():
        return
    button = page.get_by_role("button", name=CONTINUE_BUTTON_TEXT)
    if not button.count():
        raise CalendarShapeChanged(
            "this page shows neither the check-in field nor a Continue button, so there is no way "
            "to reach the availability calendar from it - the URL may no longer be valid")
    try:
        button.first.click(timeout=timeout_ms)
        _checkin_field(page).wait_for(state="visible", timeout=timeout_ms)
    except Exception as exc:
        raise CalendarShapeChanged(
            f"clicked Continue but never reached the check-in step: {exc}") from exc


def _checkin_field(page):
    """The check-in control. It is a styled DIV, not an <input> - it has no placeholder attribute,
    so it can only be found by its text.

    exact=True and .last together matter, and a loose match is a real bug, not a style point:
    a substring match ALSO matches every ancestor that contains the field, up to <main>, and
    Playwright clicks the centre of whatever it is given. Clicking the centre of <main> lands on
    the field only when the page happens to be laid out so that it does - on 2026-10-07 that was
    true for three of the four tours and false for CNX-3, whose page is short, so CNX-3 alone
    failed with 'no month calendar appeared'. exact=True drops the ancestors (their text contains
    much more than this string) and .last then takes the innermost of what remains."""
    return page.get_by_text(CHECKIN_FIELD_TEXT, exact=True).last


# The first click on the check-in field is sometimes swallowed - the page is a React app that
# re-renders around the moment it becomes interactive, and a click landing in that window does
# nothing at all. Observed both by hand in a real browser and in this script: on 2026-10-07
# CNX-3 failed every run with "no month calendar appeared" from a page that a screenshot showed
# was sitting correctly on the Check In step with the field visible, while the other three tours
# worked. Clicking again is the fix; a single attempt is simply unreliable on this page.
_OPEN_CALENDAR_ATTEMPTS = 3

_CALENDAR_IS_OPEN_JS = """() => [...document.querySelectorAll('*')].some(
     e => e.children.length === 0 && /^[A-Z][a-z]{2} 20\\d\\d$/.test((e.textContent||'').trim()))"""


def _open_calendar(page, timeout_ms: int) -> None:
    """Click the check-in field until the date dialog actually opens.

    A programmatic element.click() does not reach this control's React handler, so Playwright's
    real input events are used. If the dialog never appears after every attempt, that is raised
    rather than silently producing an empty - and falsely reassuring - 'nothing is closed'
    reading: an unopened calendar and a fully available year look identical from here, and only
    one of them is safe to act on."""
    per_attempt_ms = max(2000, timeout_ms // _OPEN_CALENDAR_ATTEMPTS)
    last_error = None
    for attempt in range(_OPEN_CALENDAR_ATTEMPTS):
        try:
            _checkin_field(page).click(timeout=timeout_ms)
        except Exception as exc:
            last_error = exc
            continue
        try:
            page.wait_for_function(_CALENDAR_IS_OPEN_JS, timeout=per_attempt_ms)
            return
        except Exception as exc:
            last_error = exc
    raise CalendarShapeChanged(
        f"the check-in field was clicked {_OPEN_CALENDAR_ATTEMPTS} times but no month calendar "
        f"ever appeared - reading this as 'nothing is closed' would be wrong, so the run stops "
        f"here. Last error: {last_error}")


def stop_sales_for_tour(page, url: str, today: Optional[_dt.date] = None,
                        from_day: int = SCAN_FROM_DAY,
                        to_day: int = SCAN_TO_DAY) -> Dict[str, Any]:
    """The full reading for one tour URL: what to write, and what was seen but deliberately not.

    Returns ranges ready for stop_sales_parser.merge_stop_sales, plus the two skipped buckets as
    ranges too, so a run log can show a human exactly what the near-term release window hid."""
    closed = closed_dates_for_url(page, url)
    buckets = split_by_window(closed, today=today, from_day=from_day, to_day=to_day)
    return {
        "url": url,
        "closed_days_seen": len(closed),
        "ranges": days_to_ranges(buckets["in_window"]),
        "skipped_near_term": days_to_ranges(buckets["skipped_near_term"]),
        "skipped_past": days_to_ranges(buckets["skipped_past"]),
    }
