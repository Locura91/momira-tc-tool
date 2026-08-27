"""CONFIRMED PRODUCT-OWNER REQUEST (2026-08-26): "after sending successfully mails with outreach,
include the balloons again for a visible sign, that mails have been sent." st.balloons() used to
fire only when the WHOLE batch was clean (zero failures) - a batch with even one per-recipient
failure among many genuine sends showed no visible "yes, mail went out" signal at all, even though
real mail reached everyone else. outreach_tool._summarize_send_log is the pure decision logic
factored out of the Streamlit render so this is testable without a Streamlit runtime.
"""
from outreach_tool import _summarize_send_log


def test_balloons_fire_on_a_fully_clean_batch():
    log = [{"status": "sent"}, {"status": "sent"}, {"status": "skipped"}]
    sent, skipped, failed, show_balloons, message, level = _summarize_send_log(log)
    assert (sent, skipped, failed) == (2, 1, 0)
    assert show_balloons is True
    assert level == "success"
    assert "2" in message and "1" in message


def test_balloons_still_fire_when_some_recipients_failed_but_others_sent():
    """The actual reported gap: a partial-failure batch used to suppress balloons entirely."""
    log = [{"status": "sent"}, {"status": "sent"}, {"status": "failed"}]
    sent, skipped, failed, show_balloons, message, level = _summarize_send_log(log)
    assert (sent, skipped, failed) == (2, 0, 1)
    assert show_balloons is True
    assert level == "warning"  # still warns about the failure, but balloons still fire


def test_no_balloons_when_nothing_actually_sent():
    log = [{"status": "skipped"}, {"status": "skipped"}]
    sent, skipped, failed, show_balloons, message, level = _summarize_send_log(log)
    assert sent == 0
    assert show_balloons is False
    assert level == "success"


def test_no_balloons_and_only_a_warning_when_everything_failed():
    log = [{"status": "failed"}, {"status": "failed"}]
    sent, skipped, failed, show_balloons, message, level = _summarize_send_log(log)
    assert show_balloons is False
    assert level == "warning"


def test_empty_log_reports_nothing():
    sent, skipped, failed, show_balloons, message, level = _summarize_send_log([])
    assert (sent, skipped, failed) == (0, 0, 0)
    assert show_balloons is False
    assert message == ""
    assert level is None
