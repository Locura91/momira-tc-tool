"""
outreach_followups.py — durable send history + reply follow-up reminders for
Find & Contact Suppliers.

CONFIRMED PRODUCT-OWNER REQUEST (2026-08-16): "is there a chance to recognize, that once an
outreach mail has been send and the supplier did not reply on the mail? I just want to make
sure, that we might must send an reminder, if no response has been send."

WHAT THIS IS NOT: automatic reply detection. The platform only ever SENDS email, via Resend or
SMTP (see outreach_email.py) - it has no access to whatever mailbox actually RECEIVES a
supplier's reply, so it has no way to know on its own whether one arrived. Building that would
mean storing real mailbox login credentials and polling an inbox, which is a materially bigger
and riskier change than this - CONFIRMED product-owner decision (2026-08-16): start with the
manual-confirm version instead.

WHAT THIS IS: two things.
  1. A DURABLE send history. Before this, a send log only ever lived in the current browser
     session and a CSV the operator downloaded by hand (see outreach_tool.py's own docstring on
     that gap) - there was nothing to build a reminder on top of. Every actual send now gets a
     row here, in platform_store, surviving across sessions and redeploys the same way the
     rest of the platform's memory does.
  2. A manual-confirm follow-up worklist: "sent N+ days ago, no reply logged yet." The operator
     checks their own inbox and either marks a row replied (it drops off the list for good) or
     sends a reminder (recorded here too, so the same row doesn't nag again the very next day -
     see pending_followups' reminder grace period).

CONFIRMED PRODUCT-OWNER DECISION (2026-08-19 audit): a row used to reset its own clock every
time a reminder went out and simply reappear as "due" again after another FOLLOWUP_DUE_DAYS,
forever, with no way to record a supplier who was actually re-contacted by hand outside this
tool. Two follow-ups from that audit:
  - Reminders are now capped at ONE. Once a reminder (or a manually logged external contact -
    they're tracked identically, see log_external_contact) has gone out, the row stops
    resurfacing in pending_followups() - it moves to cold_followups() instead, a separate,
    non-nagging "already followed up once, still no reply" list the operator can check
    whenever they want without it demanding attention.
  - log_external_contact() lets the operator say "I already emailed this supplier myself,
    outside the tool" - it resets the clock exactly like a reminder would, since from this
    tool's point of view the supplier HAS been re-contacted, just not through here.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import platform_store

# Stamped on every delivery. app.py compares this against its own build string and says so on
# screen when they differ. CONFIRMED GAP (full-app audit, already logged): this module and
# outreach_email.py were the only two of the outreach subsystem's files with no MODULE_BUILD
# constant at all, invisible to app.py's partial-deploy detector - added now.
MODULE_BUILD = "2026-09-03-google-maps-url-coordinates"

_NAMESPACE = "outreach_sends"

# CONFIRMED-REASONABLE DEFAULT (not a specific product-owner number): a working week. Short
# enough that a genuinely interested supplier isn't forgotten, long enough that a normal reply
# delay isn't flagged as "no response" the next morning.
FOLLOWUP_DUE_DAYS = 5


def _now():
    return datetime.now(timezone.utc)


def _key(email: str, sent_at: str) -> str:
    return f"{(email or '').strip().lower()}|{sent_at}"


def record_send(supplier: Dict[str, Any], session: Dict[str, Any], subject: str,
                sent_at: Optional[str] = None) -> bool:
    """One row per actual send. `sent_at` should be the dispatch_batch entry's own timestamp
    when available, so the record and the CSV log agree on when it happened."""
    email = (supplier.get("email") or "").strip().lower()
    if not email:
        return False
    sent_at = sent_at or _now().isoformat()
    return platform_store.set(_NAMESPACE, _key(email, sent_at), {
        "email": email,
        "supplier_name": supplier.get("name") or "",
        "website": supplier.get("website") or "",
        "subject": subject or "",
        "country": session.get("country") or "",
        "keyword": session.get("keyword") or "",
        # CONFIRMED BUG FIX (full-app audit HIGH, 2026-09-01): `keyword` above is a display-only
        # summary of the RUN ("12 place/theme combination(s)" for a combination/queue run) - fine
        # for the follow-up worklist's own caption, never meant to be injected into an email a
        # supplier reads. The per-SUPPLIER value that actually belongs in outbound email content
        # (outreach_email.build_template_data's [FocusKeyword]) is `supplier["foundVia"]` when
        # this send came from a combination run, captured here so a later reminder - built from
        # this stored row, long after the original run's session is gone - can reuse the exact
        # same correct value the first email used, instead of re-deriving one from `keyword` and
        # getting the same run-summary nonsense build_template_data was fixed to reject.
        "focus_keyword": supplier.get("foundVia") or session.get("keyword") or "",
        "sent_at": sent_at,
        "status": "pending",           # pending -> replied (the only other state - see module docstring)
        "reminder_sent_at": None,
        "reminder_channel": None,      # "tool" (Send reminder button) or "external" (logged by hand)
        "reply_marked_at": None,
    })


def record_sends_from_log(suppliers: List[Dict[str, Any]], session: Dict[str, Any],
                          send_log: List[Dict[str, Any]]) -> int:
    """Feed this dispatch_batch's own result list right after a real send. Only entries whose
    status is actually 'sent' get recorded - 'skipped'/'failed'/'would_send' never reached the
    supplier, so there is nothing to follow up on. Returns how many rows were recorded.

    CONFIRMED BUG FIX (full-app audit CRITICAL #4, 2026-09-01): a 'demo' send (no email provider
    configured - see outreach_email.py's send_supplier_email, provider == "demo") is fully built
    but deliberately never delivered, "so the workflow stays testable" - yet dispatch_batch still
    logs it with status "sent" (it only carries a separate demo=True flag alongside), and this
    function used to check status alone. A demo run therefore permanently recorded suppliers who
    were never actually emailed as contacted: they'd show "Contacted before" forever even though
    nothing went out, and later get a "just checking in" reminder for a first email that never
    existed. Demo entries must never reach durable send history - skip them here, the single
    real gate between dispatch_batch's in-memory log and the durable outreach_sends store."""
    by_email = {(s.get("email") or "").strip().lower(): s for s in suppliers if s.get("email")}
    recorded = 0
    for entry in (send_log or []):
        if entry.get("status") != "sent" or entry.get("demo"):
            continue
        email = (entry.get("email") or "").strip().lower()
        supplier = by_email.get(email) or {"email": email, "name": entry.get("supplierName")}
        if record_send(supplier, session, entry.get("subject", ""), entry.get("timestamp")):
            recorded += 1
    return recorded


def list_all_sends() -> List[Dict[str, Any]]:
    rows = list(platform_store.get_namespace(_NAMESPACE).values())
    return sorted(rows, key=lambda r: r.get("sent_at", ""), reverse=True)


def _days_since(iso_ts: str) -> Optional[int]:
    if not iso_ts:
        return None
    try:
        ts = datetime.fromisoformat(iso_ts)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (_now() - ts).days
    except (TypeError, ValueError):
        return None


def pending_followups(min_days: int = FOLLOWUP_DUE_DAYS) -> List[Dict[str, Any]]:
    """The operator's actual to-do list: sends still not marked replied, NEVER yet followed up
    on (no reminder, no logged external contact), where enough time has passed since the
    original send that it's worth checking on.

    CONFIRMED PRODUCT-OWNER DECISION (2026-08-19 audit): reminders are capped at one - a row
    that already got a reminder (or a manually logged external contact) does NOT come back here
    after another min_days. It moves to cold_followups() instead, so this list only ever demands
    attention for something that hasn't been followed up on at all yet."""
    out = []
    for row in list_all_sends():
        if row.get("status") == "replied" or row.get("reminder_sent_at"):
            continue
        days_since_sent = _days_since(row.get("sent_at", ""))
        if days_since_sent is None or days_since_sent < min_days:
            continue
        out.append(dict(
            row,
            key=_key(row.get("email", ""), row.get("sent_at", "")),
            days_since_sent=days_since_sent,
            days_since_last_contact=days_since_sent,
        ))
    out.sort(key=lambda r: -(r["days_since_last_contact"] or 0))
    return out


def cold_followups() -> List[Dict[str, Any]]:
    """CONFIRMED PRODUCT-OWNER DECISION (2026-08-19 audit): rows that already got their one
    reminder (or logged external contact) and still haven't been marked replied. These no
    longer nag as "due" - reminders are capped at one - but they're not thrown away either;
    this is the "already followed up once, still nothing" list an operator can check at their
    own pace, e.g. before writing the supplier off."""
    out = []
    for row in list_all_sends():
        if row.get("status") == "replied" or not row.get("reminder_sent_at"):
            continue
        out.append(dict(
            row,
            key=_key(row.get("email", ""), row.get("sent_at", "")),
            days_since_sent=_days_since(row.get("sent_at", "")),
            days_since_reminder=_days_since(row.get("reminder_sent_at", "")),
        ))
    out.sort(key=lambda r: -(r["days_since_reminder"] or 0))
    return out


def mark_replied(email: str, sent_at: str) -> bool:
    """CONFIRMED BUG FIX (full-app audit HIGH, 2026-09-01): this used to touch only the single
    (email, sent_at) row identified by the button that was clicked - keyed per SEND, not per
    SUPPLIER. A supplier can genuinely end up with more than one row here (found and contacted
    again in a later, separate run under the same email - nothing here prevents that once a
    prior row has already been marked replied, since the duplicate-send guard only checks
    UNREPLIED-looking history in practice) - a reply is a fact about the SUPPLIER, not about
    one specific email thread, so marking only the clicked row left every OTHER pending row for
    that same email still eligible for pending_followups()/a "just checking in" reminder to a
    supplier who has, in fact, already replied. Every not-yet-replied row for this email is now
    marked, not just the one the button happened to be on; `sent_at` is kept as a parameter
    (rather than dropped) only so a caller can still confirm the specific row it meant to act on
    exists before this fans out - it no longer limits which rows get updated."""
    normalized_email = (email or "").strip().lower()
    target_key = _key(email, sent_at)
    target_row = platform_store.get(_NAMESPACE, target_key)
    if not isinstance(target_row, dict):
        return False
    ok = True
    for row in list_all_sends():
        if (row.get("email") or "").strip().lower() != normalized_email:
            continue
        if row.get("status") == "replied":
            continue
        row = dict(row)
        row["status"] = "replied"
        row["reply_marked_at"] = _now().isoformat()
        ok = platform_store.set(_NAMESPACE, _key(row["email"], row["sent_at"]), row) and ok
    return ok


def mark_reminder_sent(email: str, sent_at: str, channel: str = "tool") -> bool:
    key = _key(email, sent_at)
    row = platform_store.get(_NAMESPACE, key)
    if not isinstance(row, dict):
        return False
    row["reminder_sent_at"] = _now().isoformat()
    row["reminder_channel"] = channel
    return platform_store.set(_NAMESPACE, key, row)


def log_external_contact(email: str, sent_at: str) -> bool:
    """CONFIRMED PRODUCT-OWNER DECISION (2026-08-19 audit): "I already emailed this supplier
    myself, outside the tool" - resets the clock exactly like a reminder would (same field,
    same one-reminder cap), just tagged with channel="external" so it's visibly distinguishable
    from a reminder actually sent through the tool's own Send button."""
    return mark_reminder_sent(email, sent_at, channel="external")
