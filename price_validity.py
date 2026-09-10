"""
price_validity.py — the "(YYYYMMDD)" price-validity code (product owner request, 2026-09-08).

CONFIRMED PRODUCT-OWNER REQUEST (2026-09-08): "we will add a code like '(20271031)' to the
services. This code would mean: The service is valid until 31. Oct. 2027, after that we have no
confirmed prices from the supplier... AI will detect the code in the voucher remarks and will
send once a week an update to the human, that following services XY need soon an update on the
price." Confirmed scope: Transfer, Ticket and Transport (not ClosedTour or Hotel). Confirmed
delivery (same conversation): both an in-app banner and an email, warning 60 days ahead of the
date.

WHY VOUCHER REMARKS AT ALL, NOT SOME INTERNAL-ONLY FIELD: none of ContractTicketDataSheetVO /
TransferDescriptorVO / TransportDataSheetVO carry an internal-only notes field the way Travel
Compositor's own MASTER hotel data does (IdeaHotelDataVO.internalRemark - a completely different,
master-data-only object, unrelated to any of these three - see masterdata_matcher.py). Voucher
remarks is genuinely the only place a persistent per-service note can live in Travel Compositor
at all, so embedding a parseable code in text that's already there is the only option, not a
workaround. CORRECTED (2026-09-10): Transport DOES have its own genuine voucherRemarks field,
same as Ticket/Transfer (see schemas.py's TransportDataSheetVO.voucherRemarks) - an earlier
"no separate field, folded into description" claim here was wrong, confirmed via a real
screenshot of Travel Compositor's own Transport edit screen. A one-off repair tool in
bulk_notes.py moves the code on any Transport whose code landed in description during the brief
window this was wrong.

THE CODE ITSELF: encode_price_validity_code()/extract_price_validity_date()/
strip_price_validity_code() are the parse/round-trip primitives. with_price_validity_code() is
the shared voucher-text composition step (same call-chain pattern as builder.py's own
_with_manual_notes/_with_what_to_bring) that Ticket/Transfer/Transport wire in as the LAST step,
so the code is never accidentally wrapped inside other formatting.

THE WEEKLY SCAN: scan_expiring_services() pulls every live Ticket/Transfer/Transport per supplier,
parses whatever code is embedded in its live voucher text, and flags anything already past its
date or due within WARN_AHEAD_DAYS. is_due()/mark_reviewed() mirror weekly_review.py's own
solution to "no real background cron on Streamlit Cloud" - checked opportunistically on page
load, not on a real schedule - see that module's docstring for the same reasoning. send_alert_email
reuses outreach_email.py's already-configured provider (Resend/SMTP) rather than inventing a
second one.
"""

# Stamped on every delivery - see platform_store.py's own header for why.
MODULE_BUILD = "2026-09-10-transport-supplement-no-overlap"

import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import platform_store
import outreach_email

_NAMESPACE = "price_validity_review"
_STATE_KEY = "state"

REVIEW_INTERVAL_DAYS = 7
# CONFIRMED product-owner decision (2026-09-08): warn 60 days ahead of a service's stated
# price-validity date.
WARN_AHEAD_DAYS = 60

# A code only ever needs to express a date human-typed a document confirms prices for - a
# century-wide plausible window comfortably covers every real case (supplier confirmations are
# never decades out) while still rejecting an unrelated 8-digit number (a phone number, a
# booking reference) that happens to sit in parentheses in the same text.
_MIN_PLAUSIBLE_YEAR = 2020
_MAX_PLAUSIBLE_YEAR = 2100

_CODE_RE = re.compile(r"\((\d{8})\)")

# Product types this feature applies to (confirmed scope, 2026-09-08) - not ClosedTour or Hotel.
PRODUCT_TYPES = ("Ticket", "Transfer", "Transport")


def _parse_iso(value: Any) -> Optional[date]:
    """Accepts a date, or an ISO 'YYYY-MM-DD' (or full ISO datetime) string - the shape a
    Streamlit st.date_input or a stored session value could realistically hand this."""
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _plausible(d: date) -> bool:
    return _MIN_PLAUSIBLE_YEAR <= d.year <= _MAX_PLAUSIBLE_YEAR


def encode_price_validity_code(valid_until: Any) -> str:
    """valid_until: a date, or an ISO 'YYYY-MM-DD' string. Returns the "(YYYYMMDD)" code -
    product owner's own real example, "(20271031)" = 31 Oct 2027. Returns "" for anything that
    doesn't parse as a real date, so a caller can safely do `if code:` without a separate check."""
    d = _parse_iso(valid_until)
    return f"({d.strftime('%Y%m%d')})" if d else ""


def extract_price_validity_date(text: Optional[str]) -> Optional[date]:
    """Finds the price-validity code in `text` (Ticket/Transfer voucher remarks, or Transport's
    description) and returns the date it encodes, or None if there isn't a plausible one.

    Returns the LAST plausible match, not the first: with_price_validity_code() below always
    strips any existing code before appending the current one, so under normal use there is only
    ever one - but text edited by hand outside this app (or a live record from before this
    feature existed, still carrying an old ad-hoc note) could carry more than one, and the most
    recently-stated date is the one that should win."""
    if not text:
        return None
    best = None
    for match in _CODE_RE.finditer(text):
        try:
            d = datetime.strptime(match.group(1), "%Y%m%d").date()
        except ValueError:
            continue
        if _plausible(d):
            best = d
    return best


def strip_price_validity_code(text: Optional[str]) -> str:
    """Removes every plausible "(YYYYMMDD)" code from `text`, collapsing any blank line/trailing
    whitespace the removal leaves behind. A parenthetical 8-digit number that ISN'T a plausible
    date (see _plausible) is left untouched - it's presumably something else entirely, not this
    app's code, and stripping it would be silent, unrelated data loss."""
    if not text:
        return text or ""

    def _sub(match):
        try:
            d = datetime.strptime(match.group(1), "%Y%m%d").date()
        except ValueError:
            return match.group(0)
        return "" if _plausible(d) else match.group(0)

    cleaned = _CODE_RE.sub(_sub, text)
    # Collapse runs of blank lines the removal can leave behind (the code is appended on its own
    # line by with_price_validity_code below), then trim.
    cleaned = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", cleaned)
    return cleaned.strip()


def with_price_validity_code(voucher_text: Optional[str], extracted_data: Dict[str, Any]) -> str:
    """Shared voucher-text composition step for Ticket/Transfer/Transport - same call-chain
    pattern as builder.py's own _with_manual_notes/_with_what_to_bring, meant to be the LAST
    wrapper applied (see this module's own docstring) so the code is never nested inside other
    formatting (e.g. the entrance-fee bullet, which is PREPENDED rather than appended).

    Reads extracted_data['price_valid_until_date'] - named with the '_date' suffix so it
    automatically gets ui_components.editable_field's existing DD/MM/YYYY-on-screen/ISO-stored
    date convention (see that function's own comment), the same as every other date field in
    this app, rather than inventing a second date-input convention. ALWAYS strips any code
    already present in voucher_text first - an update's existing live voucher text may already
    carry an OLD code from a previous publish - before appending the current one, so a
    re-publish REPLACES the stated date instead of piling up a second "(YYYYMMDD)" alongside the
    old one.

    If price_valid_until_date is blank, the (possibly still-code-bearing) text is returned with
    any existing code stripped - clearing the date field on an update genuinely means "we no
    longer have a confirmed validity date for this service", not "leave whatever was there
    before"."""
    base = strip_price_validity_code(voucher_text or "")
    code = encode_price_validity_code((extracted_data or {}).get("price_valid_until_date"))
    if not code:
        return base
    return f"{base}\n{code}".strip() if base else code


# ============================================================================
# WEEKLY SCAN
# ============================================================================

def _voucher_text_of(service: Dict[str, Any], product_type: str) -> str:
    """Real live-record shapes (confirmed via api_client.py's get_tickets/get_transfers/
    get_transports and their per-item GETs): Ticket/Transfer/Transport all carry their datasheet
    under ['datasheet'][lang]['voucherRemarks'] (falls back to a bare top-level 'voucherRemarks'
    for a leaner/different response shape some list endpoints return).

    CORRECTED (2026-09-10): Transport DOES have a real voucherRemarks field after all (see
    schemas.py's TransportDataSheetVO.voucherRemarks) - the earlier "no voucherRemarks, code
    lives in description instead" claim was wrong. voucherRemarks is checked FIRST for every
    product type now; Transport additionally falls back to description if voucherRemarks is
    empty, purely so this scan still finds a code on any Transport that hasn't been through
    bulk_notes.py's one-off repair yet (the 2026-09-10 bulk mis-write that landed codes in
    description before this fix) - once every Transport is repaired that fallback is dead code,
    but leaving it costs nothing and prevents a silently-missed expiry warning in the meantime."""
    if not isinstance(service, dict):
        return ""
    datasheet = service.get("datasheet")
    if isinstance(datasheet, dict):
        en = datasheet.get("EN") or next(iter(datasheet.values()), {}) or {}
        if isinstance(en, dict):
            text = en.get("voucherRemarks")
            if text:
                return text
            if product_type == "Transport" and en.get("description"):
                return en["description"]
    text = service.get("voucherRemarks")
    if text:
        return text
    if product_type == "Transport":
        return service.get("description") or ""
    return ""


def _service_label(service: Dict[str, Any]) -> str:
    if not isinstance(service, dict):
        return "(unknown)"
    datasheet = service.get("datasheet")
    if isinstance(datasheet, dict):
        en = datasheet.get("EN") or next(iter(datasheet.values()), {}) or {}
        if isinstance(en, dict) and en.get("name"):
            return en["name"]
    return service.get("name") or service.get("code") or "(unnamed)"


def _list_services(client, supplier_id: str, product_type: str) -> List[Dict[str, Any]]:
    """Best-effort - a supplier/product-type this platform can't currently list (a transient API
    error, an empty account) contributes nothing rather than aborting the whole scan; see
    scan_expiring_services' own per-supplier try/except for the same philosophy at the next level
    up."""
    try:
        if product_type == "Ticket":
            resp = client.get_tickets(supplier_id, first=0, limit=200)
            items = resp.get("tickets") if isinstance(resp, dict) else resp
        elif product_type == "Transfer":
            resp = client.get_transfers(supplier_id)
            items = resp.get("transfers") if isinstance(resp, dict) else resp
        else:  # Transport
            resp = client.get_transports(supplier_id)
            items = resp.get("transports") if isinstance(resp, dict) else resp
        return items if isinstance(items, list) else []
    except Exception:
        return []


def scan_expiring_services(client, suppliers: List[Dict[str, Any]],
                            warn_ahead_days: int = WARN_AHEAD_DAYS,
                            today: Optional[date] = None) -> List[Dict[str, Any]]:
    """Pulls every live Ticket/Transfer/Transport for each supplier, parses whichever
    price-validity code is embedded in its live voucher text, and returns one entry per service
    whose date is already past or within `warn_ahead_days`:
        {supplier_id, supplier_name, product_type, code, label, valid_until (date),
         days_remaining (negative if already expired)}
    Services with no embedded code at all are silently skipped - there is nothing to flag; this
    scan only ever reports what a human has actually stated, never guesses. Sorted soonest-first
    (already-expired services first, since those are the most urgent)."""
    today = today or datetime.now(timezone.utc).date()
    flagged: List[Dict[str, Any]] = []

    for supplier in suppliers or []:
        supplier_id = str(supplier.get("id") or "")
        supplier_name = supplier.get("commercialName") or supplier.get("legalName") or supplier_id
        if not supplier_id:
            continue
        for product_type in PRODUCT_TYPES:
            try:
                services = _list_services(client, supplier_id, product_type)
                for service in services:
                    valid_until = extract_price_validity_date(_voucher_text_of(service, product_type))
                    if not valid_until:
                        continue
                    days_remaining = (valid_until - today).days
                    if days_remaining <= warn_ahead_days:
                        flagged.append({
                            "supplier_id": supplier_id, "supplier_name": supplier_name,
                            "product_type": product_type, "code": service.get("code") or service.get("id"),
                            "label": _service_label(service), "valid_until": valid_until,
                            "days_remaining": days_remaining,
                        })
            except Exception:
                # One supplier/product-type failing to list must never abort the rest of the
                # scan - same "best effort, never crash the weekly check" philosophy as
                # weekly_review.py's own scan.
                continue

    flagged.sort(key=lambda f: f["days_remaining"])
    return flagged


# ============================================================================
# WEEKLY CADENCE (mirrors weekly_review.py's is_due/mark_reviewed exactly - see that module's
# docstring for why this is checked on page load rather than a real scheduler)
# ============================================================================

def _load_state() -> Dict[str, Any]:
    return platform_store.get(_NAMESPACE, _STATE_KEY) or {}


def is_due() -> bool:
    state = _load_state()
    last = state.get("last_reviewed_at")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - last_dt >= timedelta(days=REVIEW_INTERVAL_DAYS)


def mark_reviewed(flagged_count: int = 0) -> bool:
    state = _load_state()
    state["last_reviewed_at"] = datetime.now(timezone.utc).isoformat()
    state["last_flagged_count"] = flagged_count
    return platform_store.set(_NAMESPACE, _STATE_KEY, state)


# ============================================================================
# EMAIL DELIVERY (reuses outreach_email.py's already-configured provider - Resend or SMTP -
# rather than inventing a second one)
# ============================================================================

def alert_recipient() -> str:
    """PRICE_VALIDITY_ALERT_EMAIL, if set, else the same operator address outreach_email.py's own
    default template signature already uses - confirmed real address in this codebase, not a
    guess (see outreach_email.py)."""
    return os.getenv("PRICE_VALIDITY_ALERT_EMAIL") or "christian@momira.de"


def _format_email_body(flagged: List[Dict[str, Any]]) -> (str, str):
    lines_text = []
    rows_html = []
    for f in flagged:
        status = f"expired {abs(f['days_remaining'])} day(s) ago" if f["days_remaining"] < 0 \
            else f"expires in {f['days_remaining']} day(s)"
        lines_text.append(f"- [{f['product_type']}] {f['supplier_name']} / {f['code']} "
                           f"\"{f['label']}\" - valid until {f['valid_until'].isoformat()} ({status})")
        rows_html.append(
            f"<tr><td>{f['product_type']}</td><td>{f['supplier_name']}</td><td>{f['code']}</td>"
            f"<td>{f['label']}</td><td>{f['valid_until'].isoformat()}</td><td>{status}</td></tr>")
    text = ("The following services have a supplier-confirmed price validity that has expired or "
            "is expiring soon:\n\n" + "\n".join(lines_text))
    html = ("<p>The following services have a supplier-confirmed price validity that has expired "
            "or is expiring soon:</p><table border='1' cellpadding='4' cellspacing='0'>"
            "<tr><th>Product</th><th>Supplier</th><th>Code</th><th>Service</th>"
            "<th>Valid until</th><th>Status</th></tr>" + "".join(rows_html) + "</table>")
    return text, html


def send_alert_email(flagged: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Sends the weekly digest via whichever provider outreach_email.py is already configured
    for (Resend takes priority, SMTP fallback, 'demo' if neither is configured - see
    outreach_email.get_email_provider). Returns {'ok': bool, 'error': str|None} - never raises,
    since a failed email must not block the in-app banner from still showing (see app.py's own
    hook, which shows the banner regardless of this result)."""
    if not flagged:
        return {"ok": True, "error": None}
    to_address = alert_recipient()
    subject = f"⏰ {len(flagged)} service(s) need a price-validity check"
    text_body, html_body = _format_email_body(flagged)
    provider = outreach_email.get_email_provider()
    from_address = outreach_email.get_from_address()

    try:
        if provider == "resend":
            import requests
            res = requests.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {os.getenv('RESEND_API_KEY')}",
                         "Content-Type": "application/json"},
                json={"from": from_address, "to": [to_address], "subject": subject,
                      "html": html_body, "text": text_body},
                timeout=15,
            )
            if res.status_code >= 400:
                return {"ok": False, "error": f"HTTP {res.status_code}: {res.text[:200]}"}
            return {"ok": True, "error": None}
        if provider == "smtp":
            from email.message import EmailMessage
            msg = EmailMessage()
            msg["From"] = from_address
            msg["To"] = to_address
            msg["Subject"] = subject
            msg.set_content(text_body)
            msg.add_alternative(html_body, subtype="html")
            with outreach_email._smtp_connection() as server:
                server.send_message(msg)
            return {"ok": True, "error": None}
        return {"ok": True, "error": None}  # demo mode - nothing actually sent, not a failure
    except Exception as e:
        return {"ok": False, "error": str(e)}
