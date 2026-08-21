"""
date_format.py — one house date format on screen, one canonical format on the wire.

CONFIRMED PRODUCT-OWNER RULE: "Please use always for Date: DD/MM/YYYY."

That rule is about what a human READS AND TYPES. It cannot be about what is sent to Travel
Compositor: every date field in their API is a LocalDate, which only accepts YYYY-MM-DD, and a
payload carrying "31/12/2049" is rejected outright. So the platform keeps ISO internally, from
extraction through to publish, and converts only at the two edges where a person is involved:
DD/MM/YYYY when a date is shown, back to ISO the moment it is saved.

BOTH FUNCTIONS ACCEPT BOTH FORMATS, deliberately. There are a few dozen places in the app where
a date is rendered or read back, and a missed one must degrade to "looks wrong" rather than
"silently means a different day". Because to_iso_date() passes an already-ISO value straight
through, a save path that was never converted still stores the right date; because
to_display_date() understands ISO too, a display path that was missed shows 2026-04-03 rather
than a wrong date. Neither mistake can corrupt data - which is the whole point.

DD/MM, NEVER MM/DD. "03/04/2026" is 3 April 2026. This is not a preference, it is the only
reading consistent with the rule above, and it matters: read the American way that same string
becomes 4 March, moving a season boundary by a month with nothing on screen to show for it.
"""

# Stamped on every delivery. app.py compares this against its own build string and says
# so on screen when they differ - a partial push (one file committed, another not) used to
# surface only as a traceback whose line numbers pointed at unrelated code.
MODULE_BUILD = "2026-08-21-currency-check-duration-fix"

import re
from datetime import date, datetime

DISPLAY_FORMAT = "DD/MM/YYYY"
DISPLAY_HINT = f"({DISPLAY_FORMAT})"

_ISO_RE = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*$")
# Accepts / . or - as the separator, because people type all three: 03/04/2026, 03.04.2026.
_DMY_RE = re.compile(r"^\s*(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\s*$")
# A 2-digit year, e.g. 03/04/26. Read as 20xx: these are travel validity dates, always current
# or future, so there is no sensible 1926 reading.
_DMY_SHORT_RE = re.compile(r"^\s*(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2})\s*$")


def _valid(year, month, day):
    try:
        return date(int(year), int(month), int(day))
    except (ValueError, TypeError):
        return None


def to_iso_date(value):
    """Anything a human might type, as YYYY-MM-DD for the API. Unrecognised input is returned
    unchanged rather than blanked - losing a date the operator typed is worse than passing an
    odd string on to validation, which will name the field and stop the publish."""
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        d = value.date() if isinstance(value, datetime) else value
        return d.isoformat()
    text = str(value).strip()
    if not text:
        return ""

    m = _ISO_RE.match(text)
    if m:
        d = _valid(m.group(1), m.group(2), m.group(3))
        return d.isoformat() if d else text

    m = _DMY_RE.match(text)
    if m:
        d = _valid(m.group(3), m.group(2), m.group(1))     # DAY first - see module docstring
        return d.isoformat() if d else text

    m = _DMY_SHORT_RE.match(text)
    if m:
        d = _valid(2000 + int(m.group(3)), m.group(2), m.group(1))
        return d.isoformat() if d else text

    return text


def to_display_date(value):
    """A date as DD/MM/YYYY for the screen. Unrecognised input is returned unchanged, so a
    field holding something unexpected shows what it really holds instead of an empty cell."""
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        d = value.date() if isinstance(value, datetime) else value
        return f"{d.day:02d}/{d.month:02d}/{d.year:04d}"
    text = str(value).strip()
    if not text:
        return ""
    iso = to_iso_date(text)
    m = _ISO_RE.match(iso)
    if not m:
        return text
    d = _valid(m.group(1), m.group(2), m.group(3))
    return f"{d.day:02d}/{d.month:02d}/{d.year:04d}" if d else text


def iso_row(row, *keys):
    """A copy of `row` with the named keys converted to ISO. For save paths that write several
    dates at once, so none is forgotten."""
    out = dict(row or {})
    for key in keys:
        if key in out:
            out[key] = to_iso_date(out[key])
    return out


def display_row(row, *keys):
    """The mirror of iso_row, for building a table a human will read."""
    out = dict(row or {})
    for key in keys:
        if key in out:
            out[key] = to_display_date(out[key])
    return out
