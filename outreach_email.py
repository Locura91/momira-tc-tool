"""
outreach_email.py — email infrastructure for the outreach tool.

A faithful Python port of the Node service `server/services/emailService.js`
from the standalone momira-suppliersearch-mail app.

Three send pathways, chosen automatically from the environment - no code
change needed to switch between them (unchanged from the original):

  1. Resend (recommended) - used whenever RESEND_API_KEY is set. Sends over
     plain HTTPS rather than SMTP, so it isn't affected by hosts that block
     outbound SMTP ports on free plans.
  2. SMTP - used when RESEND_API_KEY is NOT set but SMTP_HOST/SMTP_USER/
     SMTP_PASS are. Kept for local development and as a fallback.
  3. Demo - neither configured: messages are fully built but never delivered,
     so the whole workflow stays testable.

PORTING NOTES (see outreach_discovery.py's header for the general rules; these
are the traps specific to this file):
  * JS `\\w` inside a regex is ASCII-only. Python's `\\w` matches Unicode word
    characters by default, so `[Tag]` matching is compiled with re.ASCII -
    without it, a tag like `[Ünternehmen]` would match in Python but not in
    the original, silently changing which tags get substituted.
  * `renderTemplate` leaves an unmatched tag IN PLACE rather than blanking it,
    so authoring mistakes stay visible. Empty string counts as "no value" and
    also leaves the tag - ported exactly.
  * `escapeHtml` deliberately escapes only & < > (not quotes) - kept as-is so
    output is byte-identical.

DELIBERATE ADDITION (not in the original, flagged rather than hidden):
`dispatch_batch(..., dry_run=True)` builds every message exactly as a real run
would - same template rendering, same recipient resolution, same skip logic -
and returns the full log WITHOUT contacting any provider. It's what the UI's
optional preview uses, so the preview exercises the real code path rather than
approximating it.

REMOVED FROM THE ORIGINAL: the TEST_MODE_RECIPIENTS redirect - see the note
where it used to live, further down this file.
"""

# Stamped on every delivery. app.py compares this against its own build string and says so on
# screen when they differ. CONFIRMED GAP (full-app audit, already logged): this module and
# outreach_followups.py were the only two of the outreach subsystem's files with no MODULE_BUILD
# constant at all, invisible to app.py's partial-deploy detector - added now.
MODULE_BUILD = "2026-09-03-new-batch-currency-image-state-and-geo-country"

import base64
import hashlib
import os
import re
import smtplib
import time
from datetime import date, datetime, timezone
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Any, Callable, Dict, List, Optional

import requests

_REQUEST_TIMEOUT_S = 20


# ============================================================================
# PDF ATTACHMENT - company profile, attached to every send
# ============================================================================
def _pdf_attachment_path() -> str:
    """Defaults to assets/company-profile.pdf next to this module, so it works with no
    configuration - just drop the file there. Override with PDF_ATTACHMENT_PATH."""
    configured = os.getenv("PDF_ATTACHMENT_PATH")
    if configured:
        return os.path.abspath(configured)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "company-profile.pdf")


_ATTACHMENT_FILENAME = "Momira Travel - Company Profile.pdf"


def get_pdf_status() -> Dict[str, Any]:
    """Plain status object for the UI's "will a PDF go out?" indicator."""
    path = _pdf_attachment_path()
    exists = os.path.isfile(path)
    size_kb = None
    if exists:
        try:
            size_kb = round(os.path.getsize(path) / 1024)
        except OSError:
            pass  # cosmetic only - the exists check already passed
    return {"attached": exists, "path": path, "sizeKb": size_kb}


def _read_pdf_bytes() -> Optional[bytes]:
    path = _pdf_attachment_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as e:
        print(f"[outreach_email] Could not read PDF at {path}: {e}")
        return None


# NOTE: the original had a TEST_MODE_RECIPIENTS mechanism that redirected every
# outbound email to your own inbox. It was removed at the product owner's request once
# real sending was verified working end to end, because it added a step to every run and
# a permanent "is this actually going to suppliers?" question. If TEST_MODE_RECIPIENTS
# is still set in the environment it is now INERT and ignored - delete it to avoid
# confusion. To test against yourself now, just run a search and put your own address in
# the email column of the review table.


# ============================================================================
# TRANSPORT SELECTION
# ============================================================================
def get_email_provider() -> str:
    """Resend takes priority whenever configured (plain HTTPS, works on any host).
    SMTP is the fallback, mainly for local development. Neither = demo mode."""
    if os.getenv("RESEND_API_KEY"):
        return "resend"
    if os.getenv("SMTP_HOST") and os.getenv("SMTP_USER") and os.getenv("SMTP_PASS"):
        return "smtp"
    return "demo"


def get_from_address() -> str:
    """The address mail is sent FROM, for whichever provider is active.

    DELIBERATE DEVIATION FROM THE ORIGINAL: the JS fell back to
    "outreach@momiratravel.com", an address on a domain Momira has NOT verified with
    its email provider. Providers reject (or silently drop) mail From an unverified
    domain, so that fallback couldn't ever deliver - it was the cause of a real failing
    send test. The verified sending identity is info@momira.de, so that's the fallback
    now. EMAIL_FROM still overrides it, and should be set explicitly rather than relied
    on implicitly; this default exists so a forgotten setting degrades to something that
    actually works instead of something that silently can't."""
    return (os.getenv("EMAIL_FROM") or os.getenv("SMTP_FROM") or os.getenv("SMTP_USER")
            or "info@momira.de")


def verify_transport() -> Dict[str, Any]:
    """Confirms the active provider is actually reachable/configured, for the status
    badge. Resend has no connection to open the way SMTP does, so this makes one
    lightweight real API call (listing domains) - a bad key surfaces here rather than
    only failing later on a real send."""
    provider = get_email_provider()
    common = {"provider": provider}

    if provider == "resend":
        try:
            res = requests.get(
                "https://api.resend.com/domains",
                headers={"Authorization": f"Bearer {os.getenv('RESEND_API_KEY')}"},
                timeout=_REQUEST_TIMEOUT_S,
            )
            if res.status_code == 200:
                return {"ok": True, "demo": False, **common}
            body = {}
            try:
                body = res.json()
            except ValueError:
                pass
            # A "Sending access"-scoped API key (the recommended, more secure choice -
            # it can do nothing except send mail) is deliberately forbidden from
            # listing domains, so this specific error means the key IS valid and
            # working, just correctly scoped down. Any other error is real.
            if body.get("name") == "restricted_api_key" or res.status_code in (401, 403) \
                    and "restricted" in str(body).lower():
                return {"ok": True, "demo": False, **common}
            return {"ok": False, "demo": False,
                    "error": body.get("message") or f"HTTP {res.status_code}", **common}
        except requests.RequestException as e:
            return {"ok": False, "demo": False, "error": str(e), **common}

    if provider == "demo":
        return {"ok": True, "demo": True, **common}

    try:
        with _smtp_connection() as server:
            server.noop()
        return {"ok": True, "demo": False, **common}
    except Exception as e:
        return {"ok": False, "demo": False, "error": str(e), **common}


def _smtp_connection():
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT") or "587")
    secure = (os.getenv("SMTP_SECURE") or "").lower() == "true"  # true for 465, false = STARTTLS
    user, password = os.getenv("SMTP_USER"), os.getenv("SMTP_PASS")
    if secure:
        server = smtplib.SMTP_SSL(host, port, timeout=_REQUEST_TIMEOUT_S)
    else:
        server = smtplib.SMTP(host, port, timeout=_REQUEST_TIMEOUT_S)
        server.starttls()
    server.login(user, password)
    return server


# ============================================================================
# TEMPLATE ENGINE - simple, dependency-free [Tag] substitution
# ============================================================================
# re.ASCII matters: JS's \w is ASCII-only, Python's matches Unicode by default.
# Without it a tag like [Ünternehmen] would substitute here but not in the original.
TAG_PATTERN = re.compile(r"\[(\w+)\]", re.ASCII)


def _js_string(value: Any) -> str:
    """Stringifies a value the way JavaScript's String() does.

    CAUGHT BY THE DIFFERENTIAL TEST, not by review: the original calls String(value)
    on whatever a tag resolves to, and Python's str() disagrees with it on two types
    that can realistically appear in template data -
        booleans: JS String(false) -> "false", Python str(False) -> "False"
        integral floats: JS String(1.0) -> "1", Python str(1.0) -> "1.0"
    Today every built-in tag resolves to a string, so this is latent rather than
    live - but template data is a dict anyone can extend, and an operator adding a
    numeric or boolean tag later shouldn't silently get different output than the
    tool they're replacing produced."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def render_template(text: Optional[str], data: Dict[str, Any]) -> str:
    """Replaces [TagName] tokens with values from `data`. Keys are matched
    case-insensitively, so [SupplierName] and [supplierName] resolve the same way.
    An unmatched tag is left IN PLACE rather than silently blanked, so authoring
    mistakes stay visible."""
    if not text:
        return ""
    lower_key_map = {str(k).lower(): v for k, v in (data or {}).items()}

    def _sub(match):
        value = lower_key_map.get(match.group(1).lower())
        if value is None or value == "":
            return match.group(0)  # leave the tag visible
        return _js_string(value)

    return TAG_PATTERN.sub(_sub, text)


def build_template_data(supplier: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    # CONFIRMED FIX (2026-08-19 audit): a combination/queue run's `session["keyword"]` is a
    # summary string like "12 place/theme combination(s)" - real for the run as a whole, but
    # nonsense in a sentence addressed to one supplier ("...your 12 place/theme
    # combination(s) offerings..."). Each supplier found via a combination run carries its OWN
    # `foundVia` label (e.g. "Luxor · Nile Cruise" - see _merge_one_job_result in
    # outreach_tool.py), which is what actually matched this specific supplier and is what
    # should go in the email. A plain single Country/City/Keyword search has no foundVia, so
    # session["keyword"] (the real keyword typed for that search) is still used there.
    focus_keyword = supplier.get("foundVia") or session.get("keyword")
    # CONFIRMED BUG FIX (full-app audit HIGH, 2026-09-01): two real gaps in the fix above.
    # (1) A supplier ADDED BY HAND during a combination/queue run (outreach_tool.py's "Add a
    # supplier by hand" / blank-row-in-table paths) never gets a `foundVia` at all - it isn't
    # found by a search job, so nothing ever sets it - so it fell straight through to the same
    # run-summary garbage string the 2026-08-19 fix was written to avoid. (2) A REMINDER email,
    # sent much later from outreach_followups.py's durable send-history row (see record_send
    # below), was built from a `session` reconstructed out of that stored row - which only ever
    # kept the run-level `keyword` field for display in the follow-up list, never the per-
    # supplier `foundVia` that made the first email read correctly - so a reminder for a
    # combination-run supplier hit the exact same bug the first email had already been fixed for.
    # Rather than patch each caller, the run-summary shape itself (f"{N} place/theme
    # combination(s)", the one place this string is ever generated) is recognized and treated
    # as "no real per-supplier value available" here, at the single point every caller already
    # goes through - closing both gaps (and any future one shaped like them) at once.
    if focus_keyword and re.match(r"^\d+ place/theme combination\(s\)$", str(focus_keyword)):
        focus_keyword = None
    if not focus_keyword:
        focus_keyword = "your offerings"
    # CONFIRMED FIX (2026-08-19 audit): render_template deliberately leaves an unmatched
    # [Tag] visible so a genuine authoring typo stays noticeable - but a supplier scraped
    # with no name (rather than a typo in the template) hit that same fallback and could
    # send a real client an email literally containing "[SupplierName]". A missing name is
    # a data gap, not an authoring mistake, so it gets a safe generic fallback here instead.
    return {
        "SupplierName": supplier.get("name") or "your company",
        "ContactName": supplier.get("contactName") or "there",
        "Country": session.get("country"),
        "FocusKeyword": focus_keyword,
        "Website": supplier.get("website") or "",
        "SenderName": os.getenv("SENDER_NAME") or "Momira Travel Partnerships",
    }


def html_to_plain_text(html: str) -> str:
    """Very small HTML->text fallback so plain-text parts aren't empty."""
    out = re.sub(r"<style[\s\S]*?</style>", "", html, flags=re.I)
    out = re.sub(r"<script[\s\S]*?</script>", "", out, flags=re.I)
    out = re.sub(r"<br\s*/?>", "\n", out, flags=re.I)
    out = re.sub(r"</p>", "\n\n", out, flags=re.I)
    out = re.sub(r"<[^>]+>", "", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def escape_html(value: Any) -> str:
    # Only & < > - deliberately not quotes, matching the original byte for byte.
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_LIST_LINE_PATTERN = re.compile(r"^\s*[-*]\s+")


def plain_text_to_html(text: Optional[str]) -> str:
    """Turns the plain text an operator actually edits into a reasonably nice HTML
    email at send time, so there's only ever ONE body to write - no separate HTML
    source that can silently drift out of sync. Blank-line-separated blocks become
    paragraphs; a block whose every line starts with "-" or "*" becomes a bulleted
    list; single newlines inside a paragraph become <br/>."""
    if not text:
        return ""
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n"))
    html_blocks = []
    for block in blocks:
        lines = [l for l in block.split("\n") if l.strip()]
        if not lines:
            continue
        if all(_LIST_LINE_PATTERN.search(l) for l in lines):
            items = "".join(f"<li>{escape_html(_LIST_LINE_PATTERN.sub('', l, count=1))}</li>" for l in lines)
            html_blocks.append(f'<ul style="margin:0 0 16px 0; padding-left:20px;">{items}</ul>')
        else:
            body = "<br/>".join(escape_html(l) for l in lines)
            html_blocks.append(f'<p style="margin:0 0 16px 0;">{body}</p>')
    joined = "\n".join(html_blocks)
    return ('<div style="font-family: Arial, Helvetica, sans-serif; color:#1f2937; '
            f'max-width:600px; margin:0 auto;">{joined}</div>')


# ============================================================================
# MESSAGE BUILDING + SINGLE SEND
# ============================================================================
def build_message(supplier: Dict[str, Any], session: Dict[str, Any],
                  template: Dict[str, Any]) -> Dict[str, Any]:
    """Renders one supplier's message and resolves its recipients, WITHOUT sending.

    Split out from the send call (the original did both inline) so the dry-run
    preview exercises exactly the same rendering path a real send does, rather than
    an approximation of it."""
    data = build_template_data(supplier, session)
    subject = render_template(template.get("subject"), data)

    # The plain text body is the single source of truth an operator edits - the HTML
    # sent alongside it is always freshly derived from that same text here at send
    # time, so the two can never drift. htmlBody is only a fallback for any template
    # saved before textBody existed.
    if template.get("textBody"):
        text = render_template(template["textBody"], data)
        html = plain_text_to_html(text)
    else:
        html = render_template(template.get("htmlBody"), data)
        text = html_to_plain_text(html)

    return {
        "from": get_from_address(),
        "to": [supplier["email"]] if supplier.get("email") else [],
        "replyTo": os.getenv("SMTP_REPLY_TO") or None,
        "subject": subject,
        "html": html,
        "text": text,
    }


def _resend_idempotency_key(message: Dict[str, Any]) -> str:
    """CONFIRMED BUG FIX (full-app audit HIGH, 2026-09-01): with no idempotency key, a request
    that timed out on OUR side AFTER Resend had already accepted and delivered it got logged as
    "failed" (see send_supplier_email's own comment on this exact ambiguity for a malformed
    response body - a network timeout is the same class of problem, just earlier in the round
    trip) - the operator, seeing "failed", would naturally re-tick that row and press Send
    again, and Resend would deliver a genuine duplicate cold email to the same real recipient.

    Deterministic per (recipient, exact rendered subject+body, day) rather than random per
    attempt - Resend deduplicates any request carrying a key it has already seen within its
    retention window, so an accidental retry of the literal same message on the literal same
    day is caught, while a genuinely new email (different day, or the template/content changed)
    still gets its own key and sends normally."""
    basis = "|".join([
        ",".join(message.get("to") or []),
        message.get("subject") or "",
        message.get("text") or "",
        date.today().isoformat(),
    ])
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def send_supplier_email(supplier: Dict[str, Any], session: Dict[str, Any],
                        template: Dict[str, Any]) -> Dict[str, Any]:
    provider = get_email_provider()
    message = build_message(supplier, session, template)
    pdf_bytes = _read_pdf_bytes()

    if provider == "resend":
        payload = {
            "from": message["from"],
            "to": message["to"],
            "subject": message["subject"],
            "html": message["html"],
            "text": message["text"],
        }
        if message["replyTo"]:
            payload["reply_to"] = message["replyTo"]
        if pdf_bytes:
            payload["attachments"] = [{
                "filename": _ATTACHMENT_FILENAME,
                "content": base64.b64encode(pdf_bytes).decode("ascii"),
            }]
        res = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {os.getenv('RESEND_API_KEY')}",
                     "Content-Type": "application/json",
                     "Idempotency-Key": _resend_idempotency_key(message)},
            json=payload, timeout=_REQUEST_TIMEOUT_S,
        )
        if res.status_code >= 400:
            try:
                err = res.json()
                raise RuntimeError(err.get("message") or str(err))
            except ValueError:
                raise RuntimeError(f"HTTP {res.status_code}: {res.text[:200]}")
        # CONFIRMED FIX (2026-08-19 audit): Resend already returned a non-error status here -
        # the email genuinely went out. Previously, if the response BODY then failed to parse
        # (a malformed/truncated JSON body - a different failure than a non-2xx status), the
        # ValueError propagated up through dispatch_batch's per-recipient try/except and got
        # logged as "failed", even though the message was actually sent - meaning it would
        # never be recorded by record_sends_from_log (only "sent" entries are) and a naive
        # retry would send the same supplier a real duplicate. A parse failure here can only
        # mean "we don't know the provider's message id", not "the send failed".
        try:
            body = res.json() if res.content else {}
            message_id = body.get("id")
        except ValueError:
            message_id = None
        return {"messageId": message_id, "demo": False, "provider": provider}

    if provider == "demo":
        # Fully built but never delivered, so the workflow stays testable.
        return {"messageId": None, "demo": True, "provider": provider}

    msg = EmailMessage()
    msg["From"] = message["from"]
    msg["To"] = ", ".join(message["to"])
    if message["replyTo"]:
        msg["Reply-To"] = message["replyTo"]
    msg["Subject"] = message["subject"]
    # CONFIRMED BUG FIX (full-app audit LOW, 2026-09-02): Python's EmailMessage does not set a
    # Message-ID header on its own - nothing here ever set one either, so msg.get("Message-ID")
    # always returned None and every SMTP send went out with no ID this app could later use to
    # trace it back to a specific delivery (in a mail server's own logs, or a delivery-status
    # bounce referencing it). Generated explicitly now, the same way the stdlib's own
    # smtplib/email documentation recommends.
    msg["Message-ID"] = make_msgid()
    msg.set_content(message["text"])
    msg.add_alternative(message["html"], subtype="html")
    if pdf_bytes:
        msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf",
                           filename=_ATTACHMENT_FILENAME)
    with _smtp_connection() as server:
        server.send_message(msg)
    return {"messageId": msg.get("Message-ID"), "demo": False, "provider": provider}


# ============================================================================
# BATCH DISPATCH
# ============================================================================
def _default_throttle_s() -> float:
    try:
        return int(os.getenv("EMAIL_THROTTLE_MS") or "400") / 1000.0
    except ValueError:
        return 0.4


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def dispatch_batch(suppliers: List[Dict[str, Any]], session: Dict[str, Any],
                   template: Dict[str, Any], on_progress: Optional[Callable] = None,
                   dry_run: bool = False) -> List[Dict[str, Any]]:
    """
    Sends to approved suppliers one at a time (so as not to trip provider rate
    limits), calling on_progress(entry) after every attempt so the UI can stream a
    live log.

    dry_run=True renders every message and resolves recipients exactly as a real run
    would, then records what WOULD have happened without contacting any provider -
    see this module's header for why that was added.
    """
    results: List[Dict[str, Any]] = []
    throttle_s = _default_throttle_s()
    last_index = len(suppliers) - 1
    # CONFIRMED BUG FIX (full-app audit MED, 2026-09-02): nothing deduped by email WITHIN one
    # batch - two supplier rows sharing the same address (a manually-added duplicate, the same
    # business surfacing under two different names that a name-based dedupe pass upstream missed
    # - see dedupe_candidates' own comment on exactly that risk) both got a real send, so the
    # same recipient could receive two cold emails in one click. Every occurrence after the
    # first for a given address is now skipped, not sent.
    seen_emails = set()

    for idx, supplier in enumerate(suppliers):
        started_at = _now_iso()

        # A supplier with no email is skipped - there's genuinely nowhere to send.
        if not supplier.get("email"):
            entry = {
                "supplierId": supplier.get("id"), "supplierName": supplier.get("name"),
                "email": supplier.get("email"), "status": "skipped",
                "reason": "No direct email address on file", "timestamp": started_at,
            }
            results.append(entry)
            if on_progress:
                on_progress(entry)
            continue

        normalized_email = supplier["email"].strip().lower()
        if normalized_email in seen_emails:
            entry = {
                "supplierId": supplier.get("id"), "supplierName": supplier.get("name"),
                "email": supplier.get("email"), "status": "skipped",
                "reason": "Duplicate email address already sent to earlier in this same batch",
                "timestamp": started_at,
            }
            results.append(entry)
            if on_progress:
                on_progress(entry)
            continue
        seen_emails.add(normalized_email)

        if dry_run:
            try:
                message = build_message(supplier, session, template)
                entry = {
                    "supplierId": supplier.get("id"), "supplierName": supplier.get("name"),
                    "email": supplier.get("email"), "status": "would_send",
                    "to": message["to"], "subject": message["subject"],
                    "timestamp": _now_iso(),
                }
            except Exception as e:
                entry = {
                    "supplierId": supplier.get("id"), "supplierName": supplier.get("name"),
                    "email": supplier.get("email"), "status": "failed",
                    "reason": f"Message could not be built: {e}", "timestamp": _now_iso(),
                }
            results.append(entry)
            if on_progress:
                on_progress(entry)
            continue  # no provider call, and no throttle needed

        try:
            sent = send_supplier_email(supplier, session, template)
            entry = {
                "supplierId": supplier.get("id"), "supplierName": supplier.get("name"),
                "email": supplier.get("email"), "status": "sent",
                "messageId": sent.get("messageId"), "demo": sent.get("demo"),
                "timestamp": _now_iso(),
            }
        except Exception as e:
            # Per-recipient failure is logged and the batch continues, rather than one
            # bad address aborting everything after it.
            entry = {
                "supplierId": supplier.get("id"), "supplierName": supplier.get("name"),
                "email": supplier.get("email"), "status": "failed",
                "reason": str(e), "timestamp": _now_iso(),
            }
        results.append(entry)
        if on_progress:
            on_progress(entry)

        # CONFIRMED BUG FIX (full-app audit LOW, 2026-09-02): this used to sleep after EVERY
        # recipient unconditionally, including the last one in the batch - a throttle exists to
        # pace the NEXT provider call, and there is no next call after the last recipient, so
        # that final sleep did nothing but add dead time before dispatch_batch returned (and
        # before the UI could show the batch as finished). Skipped on the last iteration now.
        if idx != last_index:
            time.sleep(throttle_s)

    return results


DEFAULT_TEMPLATE = {
    "subject": "Partnership Opportunity with Momira Travel – Portfolio Expansion & Integration",
    "textBody": """Dear [SupplierName] Team,

I hope this email finds you well.

I am reaching out on behalf of Momira Travel, a specialized tour operator focusing on high-quality travel experiences in [Country]. We are currently expanding our portfolio and are eager to feature [SupplierName] in our B2B2C tool as soon as possible.

To fast-track the integration of your product within our platform, could you please provide us with the following operational and contracting details?

- Net Price List / B2B Contract Rates for your [FocusKeyword] offerings
- Dedicated Booking Email address for reservations
- General / Reservation Phone Number
- Emergency Contact Phone Number (for operational support)
- Bank Account Details (wire transfer/banking information for payments)

We are excited about the prospect of working together and bringing a steady flow of travelers your way. In case you have any questions, please feel free to reach out at any time - I am more than happy to assist. I am also very open to a call if you prefer to discuss the setup directly.

Thank you for your time and prompt assistance. We look forward to hearing from you.

Liebe Grüße / Best regards,
Christian Hitzl

Mail: christian@momira.de

Momira Travel GmbH
Edisonstraße 23, 74076 Heilbronn
Geschäftsführer: Marcel Appolt
Handelsregister B des Amtsgerichts Stuttgart HRB 806164""",
}


# CONFIRMED PRODUCT-OWNER REQUEST (2026-08-16): a follow-up for a supplier who hasn't replied
# yet - see outreach_followups.py for the "sent N+ days ago, no reply logged" tracking this
# gets sent from. Short and low-pressure on purpose: this is the SECOND email that DMC has
# gotten from Momira, and a long one reads as impatience rather than genuine interest.
DEFAULT_REMINDER_TEMPLATE = {
    "subject": "Following up — Partnership Opportunity with Momira Travel",
    "textBody": """Dear [SupplierName] Team,

I wanted to briefly follow up on my previous email regarding a partnership opportunity between Momira Travel and [SupplierName] for our [FocusKeyword] offerings in [Country].

I understand you're likely busy, so just a gentle reminder in case it slipped through - we'd still love to hear from you and get the conversation started whenever is convenient.

Please don't hesitate to reach out with any questions, or let me know if there's a better time to connect.

Liebe Grüße / Best regards,
Christian Hitzl

Mail: christian@momira.de

Momira Travel GmbH
Edisonstraße 23, 74076 Heilbronn
Geschäftsführer: Marcel Appolt
Handelsregister B des Amtsgerichts Stuttgart HRB 806164""",
}
