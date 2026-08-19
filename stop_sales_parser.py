"""
stop_sales_parser.py — read a supplier's stop-sale email into structured date ranges.

WHAT A STOP SALE IS AND WHY THIS MATTERS: a stop sale blocks bookings for a product over
a date range. Suppliers send them by email, in prose, at short notice — "please note the
Nile cruise is not operating 12–19 August due to dry dock" — and every hour between that
email arriving and the block reaching Travel Compositor is an hour a customer can book
something that cannot be delivered. That is the failure this exists to shorten.

THE OPPOSITE FAILURE IS AS BAD AND LESS OBVIOUS: blocking dates that were never meant to
be blocked silently kills sellable inventory, and nobody notices, because a product that
quietly stops appearing in search results looks exactly like a product nobody searched for.
So this module only ever READS. It returns what it believes the email says, with a
confidence and a note about anything ambiguous, and a human confirms before anything is
written. Nothing here talks to Travel Compositor.

DATES ARE THE WHOLE PROBLEM. Supplier emails write them every possible way: "12-19 Aug",
"12/08 to 19/08", "August 12th until the 19th", "from the 12th for one week". Two rules do
most of the work and are stated hard in the prompt below:

  * 01/02 is ambiguous by nature. European suppliers mean 1 February; American ones mean
    2 January. Getting it wrong blocks the wrong month. Rather than guess, the model must
    flag it, and the human sees the flag on the review screen.
  * A year is very often missing. "12–19 August" in an email sent in July means this year;
    the same email in December means next year. The email's own date is passed in as
    context so the model can resolve it, and any assumption it makes is stated out loud.
"""
import email
import email.policy
import email.utils
import hashlib
import json
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import date_format

import ai_extractor

STOP_SALES_EXTRACTION_SYSTEM_PROMPT = """You are reading an email a DMC/hotel supplier sent to a tour
operator announcing a STOP SALE - dates on which one of their products cannot be sold or operated.

Your only job is to report what the email says. You are NOT deciding anything; a human reviews your
answer and confirms it before anything is changed. Report uncertainty rather than resolving it.

WHAT COUNTS AS A STOP SALE: any statement that a product is closed, fully booked, not operating,
unavailable, blacked out, on stop sale, sold out, or under maintenance for particular dates. Also
count a statement that sales are suspended "until further notice" - see the open-ended rule below.

WHAT DOES NOT COUNT - be strict, because blocking dates that should be sellable silently destroys
inventory and nobody notices:
- Price changes, new rates, contract renewals, seasonal rate periods.
- A REOPENING or a stop sale being LIFTED/RELEASED/CANCELLED ("the stop sale for 12-19 Aug is
  released", "we can confirm availability again"). Set "is_release": true for those and still list
  the dates, so the human can see what the supplier is releasing. NEVER report a release as a new
  stop sale.
- General availability warnings with no dates ("August is very busy").
- Dates that refer to when the email was written, a booking deadline, a payment due date, or a
  cancellation deadline.
- CONFIRMED RULE (product owner, 2026-08-16): "ALMOST FULL" is NOT a stop sale. If the email says
  availability is limited, low, running out, "almost full", "few spots left", "limited allotment
  remaining" or similar - but does NOT say the product is actually closed/sold out/fully
  booked/zero availability - there are still spots being sold, so nothing should be blocked. Set
  "is_stop_sale": false for these and explain in "notes" that the email describes limited-but-still-
  available inventory, not a closure. Do NOT list any dates in "stop_sales" for this case. Only
  treat it as a real stop sale once the email says availability has actually reached zero (fully
  booked, sold out, closed, no more availability) - "almost full" and "full" are different
  statements and must not be read as the same thing.

DATES - the part that must be right:
- Output every range as {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}, inclusive of both days.
- A single date is a range where start == end.
- The email's own sent date is given to you as context. Use it to resolve a missing year: dates
  that would otherwise fall in the past almost always mean the NEXT occurrence. State what you
  assumed in "notes".
- AMBIGUOUS NUMERIC DATES: a date like "01/02/2026" or "3/4" is genuinely ambiguous - European
  writers mean day/month, American writers mean month/day. Do NOT silently pick one. Interpret it
  as DAY/MONTH (the overwhelmingly common convention for European and Middle Eastern DMCs), set
  "date_format_ambiguous": true, and say so in "notes" so the human checks it. If the email
  contains any unambiguous date (a day above 12, or a month name), use that to infer the writer's
  convention for the whole email and say so.
- OPEN-ENDED stop sales ("closed from 1 December until further notice", "closed for the season"):
  set the start date, set "end" to the same value, set "open_ended": true, and explain in "notes".
  Do NOT invent an end date - a human decides how far to block.
- If a range is written backwards (end before start), report it as written and note it. Do not
  silently swap it.

PRODUCT IDENTIFICATION:
- product_identifier: the supplier's code for the product if the email states one, e.g. "ASW-1",
  "CAI-H1". Codes look like LETTERS-DIGIT(S). If the email only gives a NAME ("Four Seasons Cairo",
  "7-day Nile Cruise"), leave product_identifier empty and put the name in product_name_hint.
- product_type: "Hotel" if the email is about an accommodation property, "ClosedTour" if it is
  about a tour/cruise/multi-day programme. Leave empty if you genuinely cannot tell.
- affected_room: for a hotel, the exact room name the email names ("Superior Room") if it applies
  to one room type only. Leave empty if the stop sale applies to the whole property - that is the
  common case and assuming a room when none was named would under-block.
- affected_modality: for a tour, the modality/option code or category name if the email names one.
  Leave empty if it applies to the whole tour.

If the email contains NO stop sale at all, return "stop_sales": [] and say why in "notes". That is
a perfectly good answer - many supplier emails are about something else.

Output ONLY valid JSON, no markdown fences, no explanation:
{
  "is_stop_sale": true or false,
  "is_release": true or false,
  "product_identifier": "",
  "product_name_hint": "",
  "product_type": "Hotel" or "ClosedTour" or "",
  "affected_room": "",
  "affected_modality": "",
  "supplier_name_hint": "",
  "stop_sales": [
    {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD", "quote": "the exact words in the email these dates came from",
     "open_ended": false, "date_format_ambiguous": false}
  ],
  "confidence": "high" or "medium" or "low",
  "notes": "anything a human should check - assumed years, ambiguous formats, wording you were unsure about"
}"""


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_eml(raw_bytes: bytes) -> Dict[str, str]:
    """Pull subject, sender, date and plain-text body out of a .eml file.

    Prefers the text/plain part and falls back to stripping tags from text/html: supplier
    mail is very often HTML-only, and an empty body would look to the operator like the AI
    failed to find anything rather than like the file was never read."""
    msg = email.message_from_bytes(raw_bytes, policy=email.policy.default)
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                body = part.get_content()
                break
        if not body:
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    body = _strip_html(part.get_content())
                    break
    else:
        body = msg.get_content()
        if msg.get_content_type() == "text/html":
            body = _strip_html(body)
    return {
        "subject": str(msg.get("Subject") or ""),
        "from": str(msg.get("From") or ""),
        "date": str(msg.get("Date") or ""),
        "message_id": str(msg.get("Message-ID") or "").strip(),
        "body": (body or "").strip(),
    }


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html or "")
    text = re.sub(r"(?i)<br\s*/?>|</p>|</tr>|</div>", "\n", text)
    text = re.sub(r"(?i)</td>", "\t", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    return re.sub(r"[ \t]{2,}", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()


def email_fingerprint(subject: str, body: str, message_id: str = "") -> str:
    """The identity used to recognise an email already processed.

    Message-ID when there is one, because it is genuinely unique and survives the email
    being forwarded or re-pasted with different whitespace. Otherwise a hash of the
    normalised subject and body - an operator pasting the same mail twice is the ordinary
    case this has to catch, and copy-paste rarely reproduces whitespace exactly."""
    if message_id:
        return f"mid:{message_id.strip().strip('<>')}"
    norm = " ".join(f"{subject}\n{body}".split()).lower()
    return "sha:" + hashlib.sha256(norm.encode("utf-8")).hexdigest()[:32]


def _coerce_ranges(raw: Any) -> List[Dict[str, Any]]:
    """Keep only entries with two real ISO dates, and mark rather than fix anything odd.

    A malformed date reaching the apply step would either be rejected by Travel Compositor
    or - worse - accepted as something unintended, so anything unparseable is dropped here
    and reported. Backwards ranges are KEPT and flagged: a human can see and fix a swap on
    the review screen, whereas silently swapping it would hide a misread email."""
    out = []
    for item in (raw or []):
        if not isinstance(item, dict):
            continue
        start = str(item.get("start") or "").strip()
        end = str(item.get("end") or "").strip()
        if not (_ISO_DATE.match(start) and _ISO_DATE.match(end)):
            continue
        entry = {
            "start": start,
            "end": end,
            "quote": str(item.get("quote") or "").strip(),
            "open_ended": bool(item.get("open_ended")),
            "date_format_ambiguous": bool(item.get("date_format_ambiguous")),
        }
        if end < start:
            entry["reversed"] = True
        out.append(entry)
    return out


def _dedupe_ranges(ranges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen, out = set(), []
    for r in ranges:
        key = (r["start"], r["end"])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _as_iso_date(value: str) -> str:
    """Best-effort YYYY-MM-DD from an email Date header or an already-ISO string."""
    text = (value or "").strip()
    if not text:
        return ""
    if _ISO_DATE.match(text[:10]):
        return text[:10]
    try:
        parsed = email.utils.parsedate_to_datetime(text)
        return parsed.date().isoformat() if parsed else ""
    except Exception:
        return ""


def extract_stop_sales_from_email(raw_text: str, model: str = "claude-sonnet-5",
                                  subject: str = "", sent_date: str = "") -> Dict[str, Any]:
    """Read one email and return the stop sales it announces.

    `sent_date` matters more than it looks: supplier emails routinely omit the year, and
    "12-19 August" resolves to a different year depending on when it was written. Passing
    the send date lets that be resolved instead of guessed, and today's date is used as a
    reasonable stand-in when the email has no header (a pasted body usually doesn't).

    Returns a dict shaped like the prompt, always with the keys the UI reads, so a partial
    answer from the model can never surface as a KeyError on screen."""
    # Normalise the header date to ISO. A .eml carries an RFC-2822 string
    # ("Wed, 01 Jul 2026 09:00:00 +0000"); handing that to the model as-is invites it to
    # re-parse a date format when the whole point of passing it is to remove guesswork.
    context_date = _as_iso_date(sent_date) or date.today().isoformat()
    user_content = (
        f"EMAIL SENT: {context_date}\n"
        f"SUBJECT: {subject or '(none given)'}\n\n"
        f"BODY:\n{raw_text or ''}"
    )
    data = ai_extractor._call_claude(STOP_SALES_EXTRACTION_SYSTEM_PROMPT, user_content,
                                     model, max_tokens=4096) or {}

    ranges = _dedupe_ranges(_coerce_ranges(data.get("stop_sales")))
    dropped = len(_coerce_ranges(data.get("stop_sales"))) != len(data.get("stop_sales") or [])
    notes = str(data.get("notes") or "").strip()
    if dropped:
        notes = (notes + " " if notes else "") + \
            "Some date ranges came back in a form that couldn't be read and were left out - " \
            "check the email text against the list below."

    return {
        "is_stop_sale": bool(data.get("is_stop_sale")) and bool(ranges),
        "is_release": bool(data.get("is_release")),
        "product_identifier": str(data.get("product_identifier") or "").strip(),
        "product_name_hint": str(data.get("product_name_hint") or "").strip(),
        "product_type": str(data.get("product_type") or "").strip(),
        "affected_room": str(data.get("affected_room") or "").strip(),
        "affected_modality": str(data.get("affected_modality") or "").strip(),
        "supplier_name_hint": str(data.get("supplier_name_hint") or "").strip(),
        "stop_sales": ranges,
        "confidence": str(data.get("confidence") or "").strip().lower() or "low",
        "notes": notes,
        "_context_date": context_date,
    }


def warnings_for(parsed: Dict[str, Any]) -> List[str]:
    """Everything a person should look at twice before applying, in plain language.

    Surfaced as a list rather than buried in the notes field because these are the specific
    ways a stop sale goes wrong: the wrong month, an invented end date, a release mistaken
    for a block, or a range that would block more than the supplier asked for."""
    out = []
    if parsed.get("is_release"):
        out.append("This email looks like it RE-OPENS dates rather than closing them. Applying "
                   "it will REMOVE the matching dates from what is currently blocked, not add a "
                   "new block - check the wording matches that before continuing.")
    if any(r.get("date_format_ambiguous") for r in parsed.get("stop_sales", [])):
        out.append("At least one date was written numerically (e.g. 01/02) and could mean two "
                   "different months. It was read as DAY/MONTH — confirm that matches how this "
                   "supplier writes dates.")
    if any(r.get("open_ended") for r in parsed.get("stop_sales", [])):
        out.append("The email says 'until further notice' with no end date. A single day is "
                   "shown — set the end date yourself to however far you want to block.")
    if any(r.get("reversed") for r in parsed.get("stop_sales", [])):
        out.append("A range ends before it starts, which usually means the email was misread. "
                   "Fix the dates below before applying.")
    if (parsed.get("confidence") or "low") != "high":
        out.append(f"The AI rated its own reading of this email as {parsed.get('confidence')} "
                   f"confidence — read the quoted wording against each date.")
    long_ranges = [r for r in parsed.get("stop_sales", []) if _span_days(r) > 60]
    if long_ranges:
        out.append(f"{len(long_ranges)} range(s) block more than 60 days. That is unusual for a "
                   f"stop sale — check it isn't a season or a contract period being misread.")
    return out


def _span_days(r: Dict[str, Any]) -> int:
    try:
        a = datetime.strptime(r["start"], "%Y-%m-%d").date()
        b = datetime.strptime(r["end"], "%Y-%m-%d").date()
        return (b - a).days + 1
    except Exception:
        return 0


def _parse_stop_sale_date(value) -> Optional[date]:
    iso = date_format.to_iso_date(value)
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def remove_stop_sales(existing: List[Dict[str, Any]],
                      release: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The other half of a stop sale's lifecycle: a supplier re-opening dates that were
    previously blocked. CONFIRMED RULE (product owner, 2026-08-16): "New re-open selling date:
    We must remove an existing stop sale" - a release is not a new block, it undoes one.

    CONFIRMED PRODUCT-OWNER DECISION (2026-08-19 audit): a release range that only PARTIALLY
    overlaps a live block is auto-split - e.g. releasing 12-15 Aug out of a live 12-19 Aug block
    removes 12-15 and leaves 16-19 still blocked. Earlier this only ever matched an EXACT
    (start, end) pair and left any partial overlap untouched in "not_found"; that was a safer
    default while unproven, but confirmed as no longer wanted now that the behavior is live and
    understood. A release range that overlaps NOTHING live is still reported in "not_found" and
    left alone - there is nothing to split if there was never a live block there."""
    kept = [dict(s) for s in (existing or []) if isinstance(s, dict) and s.get("start")]
    removed, not_found = [], []

    for r in (release or []):
        r_start, r_end = _parse_stop_sale_date(r.get("start")), _parse_stop_sale_date(r.get("end"))
        if r_start is None or r_end is None or r_start > r_end:
            not_found.append(dict(r))
            continue

        any_overlap = False
        next_kept = []
        for s in kept:
            s_start, s_end = _parse_stop_sale_date(s.get("start")), _parse_stop_sale_date(s.get("end"))
            if s_start is None or s_end is None or s_end < r_start or s_start > r_end:
                next_kept.append(s)  # no overlap with this release range - untouched
                continue
            any_overlap = True
            # Overlap: keep whichever slice(s) of the live block fall OUTSIDE the release
            # window. Fully contained (s inside r) contributes nothing back - fully released.
            if s_start < r_start:
                next_kept.append({**s, "end": (r_start - timedelta(days=1)).isoformat()})
            if s_end > r_end:
                next_kept.append({**s, "start": (r_end + timedelta(days=1)).isoformat()})
        kept = next_kept

        if any_overlap:
            removed.append(dict(r))
        else:
            not_found.append(dict(r))

    return {"merged": kept, "removed": removed, "not_found": not_found}


def normalize_sender(raw_from: str) -> Dict[str, str]:
    """Pulls a clean email address and domain out of a raw From header
    ("DMC Nile Cruises <info@nile-dmc.com>" -> email + domain), for the
    sender -> supplier matching rule (see stop_sales_tool.py's remembered_supplier_for /
    remember_supplier_for): "Stop sale will come from a specific mail, which must be the first
    time matched to an existing supplier from our system" - every email that mail address (or,
    more robustly, that domain - the same DMC often emails from several addresses at one
    company) sends afterwards should match the same supplier automatically."""
    _, addr = email.utils.parseaddr(raw_from or "")
    addr = (addr or "").strip().lower()
    domain = addr.split("@", 1)[1] if "@" in addr else ""
    return {"email": addr, "domain": domain}


def merge_stop_sales(existing: List[Dict[str, Any]],
                     new: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Add new ranges to what is already live, keeping every existing one.

    MERGE, NEVER REPLACE. A product's live stop sales were put there by earlier emails and
    by people working in Travel Compositor directly; sending only the new ones would silently
    unblock dates that are still closed, and the product would start selling again with
    nobody having asked for it.

    An identical range already present is reported as a duplicate rather than added again,
    so applying the same email twice is a no-op instead of a growing list."""
    kept = [dict(s) for s in (existing or []) if isinstance(s, dict) and s.get("start")]
    have = {(str(s.get("start")), str(s.get("end"))) for s in kept}
    added, duplicates = [], []
    for r in (new or []):
        key = (str(r.get("start")), str(r.get("end")))
        if key in have:
            duplicates.append(dict(r))
            continue
        have.add(key)
        entry = {"start": r["start"], "end": r["end"]}
        kept.append(entry)
        added.append(entry)
    return {"merged": kept, "added": added, "duplicates": duplicates}


def to_json(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


def optional_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
