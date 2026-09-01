"""Tests for the fourth batch of HIGH findings (full-app audit, fixed 2026-09-01): the outreach
subsystem's remaining HIGH-severity items, continuing "batch by area" after the app.py
ClosedTour/Ticket-flow batch.

  1. Duplicate-send guard (_mark_already_contacted) failed OPEN on a store outage - identical
     to "nobody's ever been contacted." Fixed: it now returns (suppliers, store_reachable), and
     the review screen shows a real warning when the store didn't answer.
  2. An interrupted batch send lost its entire durable send record - record_sends_from_log ran
     once, only after dispatch_batch's loop returned normally. Fixed: each successful, non-demo
     send is now recorded incrementally inside dispatch_batch's own on_progress callback.
  3. No idempotency key on the Resend call - a timeout after a real delivery got logged
     "failed" and could be resent as a genuine duplicate. Fixed: a deterministic
     Idempotency-Key header, scoped to (recipient, exact message, day).
  4. Reminder emails (and manually-added suppliers within a combination run) could get the
     literal run-summary string ("12 place/theme combination(s)") injected as [FocusKeyword].
     Fixed: build_template_data now rejects that exact shape and falls back to a safe generic
     phrase, and record_send/the reminder button now carry the real per-supplier match reason
     through to a later reminder instead of re-deriving a wrong one.
  5. mark_replied touched only the single (email, sent_at) row the button was clicked on, not
     every row for that email - a supplier with 2+ send rows could still get a "just checking
     in" reminder after replying. Fixed: every not-yet-replied row for that email is now marked.
  6. The reminder-send button showed balloons/success and burned the one-reminder allowance even
     in demo mode, when nothing was actually delivered. Fixed: a demo result now shows a plain
     warning and does NOT call mark_reminder_sent.
  7. Aggregator-domain fingerprint collision: every supplier found only via the same aggregator
     (tripadvisor.com etc, no site of its own) fingerprinted identically on that shared domain,
     so only the first was kept per run. Fixed: an aggregator URL is never used as a fingerprint
     domain; falls back to a name-based fingerprint instead.
  8. A phone number formatted with a parenthesized trunk prefix ("+20 (0)100...") parsed as a
     "(0)" rating and hard-rejected the candidate. Fixed: a negative lookahead excludes a
     parenthesized digit immediately glued to more digits.

outreach_tool.py can be imported directly (unlike app.py) - it only touches Streamlit inside
function bodies, never at module import time - so most of this reuses real functions directly,
the same pattern test_outreach_tool_queue.py and test_outreach_followups.py already use. The
two fixes that only exist inside a Streamlit button's rendering code (incremental send
recording, the demo-mode reminder banner) are covered by reading app... no - by reading
outreach_tool.py's own source text, since exercising a live st.button click isn't available in
this suite either.
"""
import os
from datetime import datetime, timedelta, timezone

import outreach_discovery as od
import outreach_email as oe
import outreach_followups as ofw
import outreach_tool as ot
import platform_store


_OUTREACH_TOOL_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "outreach_tool.py")


def _read_outreach_tool_py():
    with open(_OUTREACH_TOOL_PY, "r", encoding="utf-8") as f:
        return f.read()


def _iso(days_ago=0):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


# ======================================================================
# 1. Duplicate-send guard failing open on a store outage
# ======================================================================
def test_mark_already_contacted_returns_a_tuple_with_store_health():
    suppliers = [{"name": "Brand New", "email": "brandnew@batch4-dupe-test.example",
                  "selected": True}]
    result, store_ok = ot._mark_already_contacted(suppliers)
    assert isinstance(result, list)
    assert store_ok is True  # the real local/test store answers fine


def test_mark_already_contacted_reports_store_unreachable_via_health(monkeypatch):
    """Simulates a store outage without actually breaking the shared test database - patches
    list_all_sends (empty, same as a real outage) and platform_store.health (reports down)."""
    monkeypatch.setattr(ot.ofw, "list_all_sends", lambda: [])
    monkeypatch.setattr(ot.platform_store, "health",
                        lambda: {"ok": False, "detail": "simulated outage"})
    suppliers = [{"name": "Anyone", "email": "anyone@batch4-dupe-test.example", "selected": True}]
    result, store_ok = ot._mark_already_contacted(suppliers)
    assert store_ok is False
    # Still returns the suppliers unmodified rather than erroring - the caller decides what to
    # do with a failed health check, not this function.
    assert result[0]["selected"] is True


def test_review_screen_warns_when_the_duplicate_check_could_not_be_verified():
    source = _read_outreach_tool_py()
    assert 'result.get("duplicate_check_unavailable")' in source
    idx = source.index('result.get("duplicate_check_unavailable")')
    window = source[idx:idx + 400]
    assert "st.warning" in window
    assert "Contacted before" in window


# ======================================================================
# 2. Interrupted batch losing the entire send log
# ======================================================================
def test_on_progress_records_each_sent_entry_immediately_not_after_the_whole_batch():
    source = _read_outreach_tool_py()
    marker = "def on_progress(entry):"
    idx = source.index(marker)
    window = source[idx:idx + 2000]
    assert 'ofw.record_send(' in window, (
        "on_progress must record a durable send row for each entry as dispatch_batch reports "
        "it, not wait for the whole batch to finish"
    )
    assert 'entry.get("status") == "sent"' in window
    assert 'not entry.get("demo")' in window, (
        "a demo send (no provider configured) must never reach durable history, matching "
        "CRITICAL #4's fix for the same distinction in record_sends_from_log"
    )


def test_the_stale_after_the_fact_record_sends_from_log_call_is_gone():
    """The old call recorded the WHOLE batch's history only after dispatch_batch's blocking
    loop returned - which is exactly what an interruption mid-batch used to lose. It must not
    still be sitting right after the dispatch_batch call (on_progress now does this work)."""
    source = _read_outreach_tool_py()
    dispatch_idx = source.index("st.session_state.or_send_log = oe.dispatch_batch(")
    after = source[dispatch_idx:dispatch_idx + 500]
    assert "ofw.record_sends_from_log(selected, session, st.session_state.or_send_log)" not in after


# ======================================================================
# 3. No idempotency key on the Resend call
# ======================================================================
def test_resend_idempotency_key_is_deterministic_for_the_same_message_same_day():
    message = {"to": ["supplier@batch4-idem-test.example"], "subject": "Hi",
              "text": "Body text"}
    key1 = oe._resend_idempotency_key(message)
    key2 = oe._resend_idempotency_key(message)
    assert key1 == key2
    assert len(key1) == 64  # sha256 hex digest


def test_resend_idempotency_key_differs_for_a_different_recipient_or_body():
    base = {"to": ["a@batch4-idem-test.example"], "subject": "Hi", "text": "Body"}
    other_recipient = dict(base, to=["b@batch4-idem-test.example"])
    other_body = dict(base, text="Different body")
    assert oe._resend_idempotency_key(base) != oe._resend_idempotency_key(other_recipient)
    assert oe._resend_idempotency_key(base) != oe._resend_idempotency_key(other_body)


def test_resend_call_sends_the_idempotency_key_header():
    source = os.path.join(os.path.dirname(_OUTREACH_TOOL_PY), "outreach_email.py")
    with open(source, "r", encoding="utf-8") as f:
        content = f.read()
    idx = content.index('"https://api.resend.com/emails"')
    window = content[idx:idx + 400]
    assert '"Idempotency-Key": _resend_idempotency_key(message)' in window


# ======================================================================
# 4. [FocusKeyword] template leaks - reminders and manual adds in a combination run
# ======================================================================
def test_combination_run_summary_string_never_reaches_focus_keyword():
    supplier = {"name": "Manually Added", "email": "manual@batch4-focus-test.example"}
    session = {"country": "Morocco", "keyword": "12 place/theme combination(s)"}
    data = oe.build_template_data(supplier, session)
    assert data["FocusKeyword"] == "your offerings"
    assert "combination(s)" not in data["FocusKeyword"]


def test_a_real_foundvia_still_wins_and_is_used_verbatim():
    supplier = {"name": "Found Via Search", "email": "found@batch4-focus-test.example",
               "foundVia": "Luxor · Nile Cruise"}
    session = {"country": "Egypt", "keyword": "12 place/theme combination(s)"}
    data = oe.build_template_data(supplier, session)
    assert data["FocusKeyword"] == "Luxor · Nile Cruise"


def test_a_plain_single_search_keyword_is_unaffected():
    supplier = {"name": "Plain Search Supplier", "email": "plain@batch4-focus-test.example"}
    session = {"country": "Jordan", "keyword": "Desert Safari"}
    data = oe.build_template_data(supplier, session)
    assert data["FocusKeyword"] == "Desert Safari"


def test_record_send_stores_the_resolved_focus_keyword_for_later_reminders():
    supplier = {"name": "Combo Supplier", "email": "combo@batch4-focus-test.example",
               "foundVia": "Cairo · Day Tours"}
    session = {"country": "Egypt", "keyword": "5 place/theme combination(s)"}
    sent_at = _iso(0)
    assert ofw.record_send(supplier, session, "Subject", sent_at=sent_at)
    rows = ofw.list_all_sends()
    match = [r for r in rows if r["email"] == "combo@batch4-focus-test.example"
            and r["sent_at"] == sent_at]
    assert len(match) == 1
    assert match[0]["focus_keyword"] == "Cairo · Day Tours"
    # The display-only run-summary string is still kept separately for the follow-up list's
    # own caption - just never used as FocusKeyword content.
    assert match[0]["keyword"] == "5 place/theme combination(s)"


def test_reminder_button_passes_the_stored_focus_keyword_as_foundvia():
    source = _read_outreach_tool_py()
    idx = source.index('"📨 Send reminder"')
    window = source[idx:idx + 1600]
    assert '"foundVia": row.get("focus_keyword") or ""' in window


# ======================================================================
# 5. mark_replied per-send-row instead of per-supplier
# ======================================================================
def test_mark_replied_marks_every_row_for_the_same_email_not_just_the_clicked_one():
    email = "tworows@batch4-replied-test.example"
    sent_at_1 = _iso(10)
    sent_at_2 = _iso(3)
    supplier = {"name": "Two Rows Supplier", "email": email}
    session = {"country": "Peru", "keyword": "Trekking"}
    assert ofw.record_send(supplier, session, "First email", sent_at=sent_at_1)
    assert ofw.record_send(supplier, session, "Second email", sent_at=sent_at_2)

    assert ofw.mark_replied(email, sent_at_2)  # operator clicks "replied" on the SECOND row

    rows = [r for r in ofw.list_all_sends() if r["email"] == email]
    assert len(rows) == 2
    assert all(r["status"] == "replied" for r in rows), (
        "a reply is a fact about the SUPPLIER - every row for that email must be marked, not "
        "just the one row the button happened to be on"
    )


def test_mark_replied_still_returns_false_for_an_email_with_no_matching_row():
    assert ofw.mark_replied("nobody@batch4-replied-test.example", _iso(0)) is False


# ======================================================================
# 6. Reminder screen showing success/balloons even in demo mode
# ======================================================================
def test_demo_mode_reminder_result_does_not_mark_reminder_sent():
    source = _read_outreach_tool_py()
    idx = source.index('reminder_result.get("demo")')
    window = source[idx:idx + 1500]
    # The demo branch shows a warning and does NOT reach mark_reminder_sent/balloons - those
    # must sit in the else branch instead.
    demo_branch = window[:window.index("else:")]
    assert "st.warning" in demo_branch
    assert "mark_reminder_sent" not in demo_branch
    assert "st.balloons" not in demo_branch
    else_branch = window[window.index("else:"):]
    assert "ofw.mark_reminder_sent(" in else_branch
    assert "st.balloons()" in else_branch


# ======================================================================
# 7. Aggregator-domain fingerprint collision
# ======================================================================
def _fresh_stats():
    return {"raw": 0, "after_prefilter": 0, "final": 0, "used_mock_provider": False}


def _job_result(suppliers, raw=5, after_prefilter=3, final=None, used_mock=False):
    return {
        "suppliers": suppliers,
        "stats": {"raw": raw, "after_prefilter": after_prefilter,
                  "final": final if final is not None else len(suppliers),
                  "used_mock_provider": used_mock},
    }


def test_two_different_suppliers_on_the_same_aggregator_domain_both_survive():
    suppliers = [
        {"name": "Sahara Desert Tours", "email": "a@batch4-agg-test.example",
         "website": None, "listingUrl": "https://www.tripadvisor.com/Attraction_Review-g1-a1"},
        {"name": "Nile River Cruises", "email": "b@batch4-agg-test.example",
         "website": None, "listingUrl": "https://www.tripadvisor.com/Attraction_Review-g1-a2"},
    ]
    merged, seen, stats = [], set(), _fresh_stats()
    ot._merge_one_job_result(merged, seen, stats, "Cairo · Tours", _job_result(suppliers))
    assert len(merged) == 2, (
        "two genuinely different businesses listed on the same aggregator must not collapse "
        "into one just because they share tripadvisor.com as their only 'domain'"
    )


def test_a_real_business_website_still_dedupes_normally():
    suppliers = [
        {"name": "Desert Co", "email": "a@batch4-agg-test2.example", "website": "https://desertco.example"},
        {"name": "Desert Co Again", "email": "b@batch4-agg-test2.example", "website": "https://desertco.example"},
    ]
    merged, seen, stats = [], set(), _fresh_stats()
    ot._merge_one_job_result(merged, seen, stats, "Cairo · Tours", _job_result(suppliers))
    assert len(merged) == 1, "a real shared business website must still dedupe as before"


def test_aggregator_url_helper_is_reused_not_reimplemented():
    source = _read_outreach_tool_py()
    assert "od.is_aggregator_url(website)" in source
    assert "od.is_aggregator_url(listing_url)" in source


# ======================================================================
# 8. Phone number parsed as a 0-star rating
# ======================================================================
def test_phone_number_trunk_prefix_is_not_parsed_as_a_rating():
    assert od.parse_rating("Call us at +20 (0)100-234-5678 for bookings") is None


def test_real_parenthesized_rating_still_parses():
    assert od.parse_rating("Highly rated (4.6) with 128 reviews") == 4.6
    assert od.parse_rating("Perfect score (5) this month") == 5.0
