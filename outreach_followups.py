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
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import platform_store

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
        "sent_at": sent_at,
        "status": "pending",           # pending -> replied (the only other state - see module docstring)
        "reminder_sent_at": None,
        "reply_marked_at": None,
    })


def record_sends_from_log(suppliers: List[Dict[str, Any]], session: Dict[str, Any],
                          send_log: List[Dict[str, Any]]) -> int:
    """Feed this dispatch_batch's own result list right after a real send. Only entries whose
    status is actually 'sent' get recorded - 'skipped'/'failed'/'would_send' never reached the
    supplier, so there is nothing to follow up on. Returns how many rows were recorded."""
    by_email = {(s.get("email") or "").strip().lower(): s for s in suppliers if s.get("email")}
    recorded = 0
    for entry in (send_log or []):
        if entry.get("status") != "sent":
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
    """The operator's actual to-do list: sends still not marked replied, where enough time has
    passed since the LAST contact (the original send, or a reminder if one already went out -
    see the reminder grace period below) that it's worth checking on.

    A reminder already sent resets the clock rather than the row disappearing forever - a
    supplier who ignored one email might still reply to a second, so it comes back onto the
    list after another min_days rather than being silently dropped."""
    out = []
    for row in list_all_sends():
        if row.get("status") == "replied":
            continue
        last_contact_at = row.get("reminder_sent_at") or row.get("sent_at", "")
        days_since_contact = _days_since(last_contact_at)
        if days_since_contact is None or days_since_contact < min_days:
            continue
        out.append(dict(
            row,
            key=_key(row.get("email", ""), row.get("sent_at", "")),
            days_since_sent=_days_since(row.get("sent_at", "")),
            days_since_last_contact=days_since_contact,
        ))
    out.sort(key=lambda r: -(r["days_since_last_contact"] or 0))
    return out


def mark_replied(email: str, sent_at: str) -> bool:
    key = _key(email, sent_at)
    row = platform_store.get(_NAMESPACE, key)
    if not isinstance(row, dict):
        return False
    row["status"] = "replied"
    row["reply_marked_at"] = _now().isoformat()
    return platform_store.set(_NAMESPACE, key, row)


def mark_reminder_sent(email: str, sent_at: str) -> bool:
    key = _key(email, sent_at)
    row = platform_store.get(_NAMESPACE, key)
    if not isinstance(row, dict):
        return False
    row["reminder_sent_at"] = _now().isoformat()
    return platform_store.set(_NAMESPACE, key, row)
