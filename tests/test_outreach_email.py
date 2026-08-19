"""Tests for outreach_email.py's template data / send-parsing fixes from the 2026-08-19 audit.

1. build_template_data used to always take FocusKeyword from session["keyword"] - fine for a
   plain single Country/City/Keyword search, but a combination/queue run (outreach_tool.py) sets
   session["keyword"] to a summary string like "12 place/theme combination(s)", so every email
   from a combination run read "...your 12 place/theme combination(s) offerings...". Each
   supplier found via a combination run carries its own `foundVia` label (the actual place/theme
   that matched it) - that's now preferred when present.

2. A supplier scraped with no name used to leave the literal "[SupplierName]" tag visible in a
   real outbound email (render_template deliberately leaves an unmatched tag visible for
   authoring-typo cases) - now falls back to a generic phrase instead.

3. send_supplier_email (Resend path) used to call res.json() unguarded on a 2xx response - a
   parse failure there used to look identical to a real send failure (wrongly logged "failed"
   even though the message actually went out), and is now tolerated (messageId just comes back
   None instead of raising).
"""
from unittest.mock import patch

import requests

import outreach_email as oe


def test_build_template_data_prefers_found_via_over_session_keyword():
    supplier = {"name": "Luxor Nile Tours", "foundVia": "Luxor · Nile Cruise"}
    session = {"country": "Egypt", "keyword": "12 place/theme combination(s)"}
    data = oe.build_template_data(supplier, session)
    assert data["FocusKeyword"] == "Luxor · Nile Cruise"


def test_build_template_data_falls_back_to_session_keyword_for_a_plain_search():
    supplier = {"name": "Cairo Day Trips"}
    session = {"country": "Egypt", "keyword": "Pyramids Tour"}
    data = oe.build_template_data(supplier, session)
    assert data["FocusKeyword"] == "Pyramids Tour"


def test_build_template_data_falls_back_supplier_name_when_missing():
    supplier = {"name": ""}
    session = {"country": "Egypt", "keyword": "Nile Cruise"}
    data = oe.build_template_data(supplier, session)
    assert data["SupplierName"] == "your company"


def test_build_template_data_uses_real_name_when_present():
    supplier = {"name": "Sahara Tours"}
    session = {"country": "Egypt", "keyword": "Desert Safari"}
    data = oe.build_template_data(supplier, session)
    assert data["SupplierName"] == "Sahara Tours"


def _resend_response(status_code, text):
    res = requests.Response()
    res.status_code = status_code
    res._content = text.encode("utf-8")
    return res


def test_send_supplier_email_tolerates_a_malformed_success_body(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    with patch("outreach_email.requests.post", return_value=_resend_response(200, "<html>oops</html>")), \
         patch("outreach_email._read_pdf_bytes", return_value=None):
        result = oe.send_supplier_email(
            {"name": "Test Supplier", "email": "test@example.com"},
            {"country": "Egypt", "keyword": "Nile Cruise"},
            oe.DEFAULT_TEMPLATE,
        )
    # A malformed body on a successful send must NOT raise - the message really went out.
    assert result["messageId"] is None
    assert result["demo"] is False


def test_send_supplier_email_still_raises_on_a_real_error_status(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    with patch("outreach_email.requests.post", return_value=_resend_response(400, '{"message": "bad request"}')), \
         patch("outreach_email._read_pdf_bytes", return_value=None):
        try:
            oe.send_supplier_email(
                {"name": "Test Supplier", "email": "test@example.com"},
                {"country": "Egypt", "keyword": "Nile Cruise"},
                oe.DEFAULT_TEMPLATE,
            )
            assert False, "expected a RuntimeError"
        except RuntimeError as e:
            assert "bad request" in str(e)
