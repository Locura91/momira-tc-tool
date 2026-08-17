"""Tests for outreach_followups.py — durable send history + manual-confirm follow-up worklist.

CONFIRMED PRODUCT-OWNER REQUEST (2026-08-16): "is there a chance to recognize, that once an
outreach mail has been send and the supplier did not reply on the mail? I just want to make
sure, that we might must send an reminder, if no response has been send." Chris chose the
manual-confirm approach over automatic reply detection - see the module docstring.

Each test uses a unique email/timestamp pair so tests don't collide with each other or with
real data in the shared platform_store (same pattern as test_stop_sales.py).
"""
from datetime import datetime, timedelta, timezone

import outreach_followups as ofw


def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


# ======================================================================
# record_send / record_sends_from_log
# ======================================================================
def test_record_send_requires_an_email():
    supplier = {"name": "No Email Supplier"}
    session = {"country": "Egypt", "keyword": "Nile Cruise"}
    assert ofw.record_send(supplier, session, "Subject") is False


def test_record_send_stores_a_pending_row():
    supplier = {"name": "Sahara Tours", "email": "contact@sahara-followup-test.example",
                "website": "sahara.example"}
    session = {"country": "Egypt", "keyword": "Desert Safari"}
    sent_at = _iso(0)
    assert ofw.record_send(supplier, session, "Partnership Opportunity", sent_at=sent_at)
    rows = ofw.list_all_sends()
    match = [r for r in rows if r["email"] == "contact@sahara-followup-test.example"
            and r["sent_at"] == sent_at]
    assert len(match) == 1
    assert match[0]["status"] == "pending"
    assert match[0]["reminder_sent_at"] is None


def test_record_sends_from_log_only_records_actual_sends():
    suppliers = [
        {"name": "Sent Supplier", "email": "sent@followup-log-test.example"},
        {"name": "Skipped Supplier", "email": "skipped@followup-log-test.example"},
        {"name": "Failed Supplier", "email": "failed@followup-log-test.example"},
    ]
    session = {"country": "Jordan", "keyword": "Petra Tours"}
    sent_at = _iso(0)
    send_log = [
        {"status": "sent", "email": "sent@followup-log-test.example",
         "subject": "Hello", "timestamp": sent_at},
        {"status": "skipped", "email": "skipped@followup-log-test.example",
         "subject": "Hello", "timestamp": sent_at},
        {"status": "failed", "email": "failed@followup-log-test.example",
         "subject": "Hello", "timestamp": sent_at},
    ]
    recorded = ofw.record_sends_from_log(suppliers, session, send_log)
    assert recorded == 1
    rows = ofw.list_all_sends()
    emails = {r["email"] for r in rows if r["sent_at"] == sent_at}
    assert "sent@followup-log-test.example" in emails
    assert "skipped@followup-log-test.example" not in emails
    assert "failed@followup-log-test.example" not in emails


# ======================================================================
# pending_followups
# ======================================================================
def test_pending_followups_excludes_rows_below_min_days():
    supplier = {"name": "Too Recent", "email": "recent@followup-due-test.example"}
    session = {"country": "Morocco", "keyword": "Day Trips"}
    sent_at = _iso(1)
    ofw.record_send(supplier, session, "Subject", sent_at=sent_at)
    due = ofw.pending_followups(min_days=5)
    assert not any(r["email"] == "recent@followup-due-test.example" and r["sent_at"] == sent_at
                  for r in due)


def test_pending_followups_includes_rows_at_or_past_min_days():
    supplier = {"name": "Old Enough", "email": "old@followup-due-test.example"}
    session = {"country": "Morocco", "keyword": "Day Trips"}
    sent_at = _iso(6)
    ofw.record_send(supplier, session, "Subject", sent_at=sent_at)
    due = ofw.pending_followups(min_days=5)
    match = [r for r in due if r["email"] == "old@followup-due-test.example" and r["sent_at"] == sent_at]
    assert len(match) == 1
    assert match[0]["days_since_sent"] >= 6


def test_pending_followups_excludes_replied_rows():
    supplier = {"name": "Already Replied", "email": "replied@followup-due-test.example"}
    session = {"country": "Morocco", "keyword": "Day Trips"}
    sent_at = _iso(10)
    ofw.record_send(supplier, session, "Subject", sent_at=sent_at)
    ofw.mark_replied("replied@followup-due-test.example", sent_at)
    due = ofw.pending_followups(min_days=5)
    assert not any(r["email"] == "replied@followup-due-test.example" for r in due)


def test_pending_followups_reminder_grace_period_pushes_due_date_out():
    supplier = {"name": "Reminded Already", "email": "reminded@followup-due-test.example"}
    session = {"country": "Morocco", "keyword": "Day Trips"}
    sent_at = _iso(20)
    ofw.record_send(supplier, session, "Subject", sent_at=sent_at)
    # A reminder sent just now means the clock restarts from the reminder, not the original send.
    ofw.mark_reminder_sent("reminded@followup-due-test.example", sent_at)
    due = ofw.pending_followups(min_days=5)
    assert not any(r["email"] == "reminded@followup-due-test.example" and r["sent_at"] == sent_at
                  for r in due)


def test_pending_followups_sorts_by_days_since_last_contact_descending():
    session = {"country": "Tunisia", "keyword": "Coastal Tours"}
    ofw.record_send({"name": "Newer", "email": "newer@followup-sort-test.example"},
                    session, "Subject", sent_at=_iso(6))
    ofw.record_send({"name": "Older", "email": "older@followup-sort-test.example"},
                    session, "Subject", sent_at=_iso(15))
    due = ofw.pending_followups(min_days=5)
    emails_in_order = [r["email"] for r in due
                       if r["email"] in ("newer@followup-sort-test.example",
                                        "older@followup-sort-test.example")]
    assert emails_in_order.index("older@followup-sort-test.example") \
        < emails_in_order.index("newer@followup-sort-test.example")


# ======================================================================
# mark_replied / mark_reminder_sent
# ======================================================================
def test_mark_replied_returns_false_for_nonexistent_row():
    assert ofw.mark_replied("nobody@nonexistent-followup-test.example", _iso(0)) is False


def test_mark_reminder_sent_returns_false_for_nonexistent_row():
    assert ofw.mark_reminder_sent("nobody@nonexistent-followup-test.example", _iso(0)) is False


def test_mark_replied_updates_status_and_timestamp():
    supplier = {"name": "Mark Me", "email": "markme@followup-mark-test.example"}
    session = {"country": "Greece", "keyword": "Island Hopping"}
    sent_at = _iso(0)
    ofw.record_send(supplier, session, "Subject", sent_at=sent_at)
    assert ofw.mark_replied("markme@followup-mark-test.example", sent_at)
    rows = ofw.list_all_sends()
    match = [r for r in rows if r["email"] == "markme@followup-mark-test.example"
            and r["sent_at"] == sent_at]
    assert match[0]["status"] == "replied"
    assert match[0]["reply_marked_at"]


def test_mark_reminder_sent_sets_reminder_timestamp():
    supplier = {"name": "Remind Me", "email": "remindme@followup-mark-test.example"}
    session = {"country": "Greece", "keyword": "Island Hopping"}
    sent_at = _iso(0)
    ofw.record_send(supplier, session, "Subject", sent_at=sent_at)
    assert ofw.mark_reminder_sent("remindme@followup-mark-test.example", sent_at)
    rows = ofw.list_all_sends()
    match = [r for r in rows if r["email"] == "remindme@followup-mark-test.example"
            and r["sent_at"] == sent_at]
    assert match[0]["reminder_sent_at"]
