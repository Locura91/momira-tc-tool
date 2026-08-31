"""CONFIRMED PRODUCT-OWNER REQUEST (2026-08-31): "once send mails to supplier, I dont see any
balloons - but we should display it, if the outreach mail was successfully send. We must
visualize it for the human."

Investigation found the batch "review and send" screen's balloons were already fixed and working
(2026-08-26: st.balloons() fires whenever _summarize_send_log(...) reports at least one message
actually sent, via the send-then-rerun flow in _render_review_and_send - confirmed unchanged and
still wired to render_outreach_tool()'s "review" phase dispatch).

The real, previously-unfixed gap: outreach_tool._render_followup_row()'s "Send reminder" button
is a SEPARATE real email dispatch (oe.send_supplier_email, not oe.dispatch_batch) used from the
follow-ups worklist - it only ever showed a small st.success("Reminder sent.") text with no
balloons at all, unlike the batch flow. Since it's Streamlit UI code with a real send side effect
(no pure decision function to unit test, unlike _summarize_send_log), this is checked via source
text rather than by driving a Streamlit runtime or sending real mail - same reasoning as
test_2026_08_31_silent_image_extraction_failures.py's app.py checks and
test_2026_08_31_closedtour_child_discount_visibility.py's wiring guard.
"""
import os

import outreach_tool


_OUTREACH_TOOL_PY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outreach_tool.py"
)


def _read_outreach_tool_py():
    with open(_OUTREACH_TOOL_PY, "r", encoding="utf-8") as f:
        return f.read()


def _reminder_send_success_block():
    """Isolates the try/except around the reminder send, from `try:` through the `st.rerun()`
    that ends the success branch - narrow enough that unrelated st.balloons() calls elsewhere in
    the file (e.g. the batch-send path) can't make this test pass by accident."""
    src = _read_outreach_tool_py()
    start = src.index("oe.send_supplier_email(supplier, session, oe.DEFAULT_REMINDER_TEMPLATE)")
    end = src.index("with cols[2]:", start)
    return src[start:end]


def test_reminder_send_success_path_calls_balloons():
    block = _reminder_send_success_block()
    assert "st.balloons()" in block, (
        "the reminder-send success branch must call st.balloons() - a real email dispatch "
        "through the tool, exactly like the batch send flow"
    )


def test_reminder_send_success_path_still_shows_success_message():
    """Balloons are an addition, not a replacement - the existing text confirmation must stay."""
    block = _reminder_send_success_block()
    assert 'st.success("Reminder sent.")' in block


def test_balloons_called_before_rerun_in_reminder_path():
    """st.rerun() halts the script immediately - balloons must be queued before it, not after
    (an st.balloons() placed after st.rerun() would be dead code, never reached)."""
    block = _reminder_send_success_block()
    assert block.index("st.balloons()") < block.index("st.rerun()")


def test_reminder_failure_path_does_not_get_balloons():
    """A failed send (oe.send_supplier_email raising) must not celebrate - only the except
    branch's st.error(...) should fire, no balloons anywhere in the try/except failure path."""
    src = _read_outreach_tool_py()
    start = src.index("oe.send_supplier_email(supplier, session, oe.DEFAULT_REMINDER_TEMPLATE)")
    except_start = src.index("except Exception as exc:", start)
    else_start = src.index("else:", except_start)
    failure_block = src[except_start:else_start]
    assert "st.balloons()" not in failure_block
    assert "st.error(" in failure_block


def test_batch_send_balloons_still_wired_unchanged():
    """Regression guard for the OTHER (already-fixed, 2026-08-26) balloons path - make sure this
    change didn't accidentally touch or duplicate it. Counts only actual calls, not the comment
    line above the new call that mentions st.balloons() by name for context."""
    src = _read_outreach_tool_py()
    real_calls = [line for line in src.splitlines()
                  if line.strip() == "st.balloons()"]
    assert len(real_calls) == 2  # batch-send path + the new reminder-send path


def test_summarize_send_log_still_importable_and_unchanged_in_behavior():
    """Sanity check that the pre-existing batch-send decision logic wasn't touched this round."""
    log = [{"status": "sent"}, {"status": "failed"}]
    sent, skipped, failed, show_balloons, message, level = outreach_tool._summarize_send_log(log)
    assert (sent, skipped, failed) == (1, 0, 1)
    assert show_balloons is True
