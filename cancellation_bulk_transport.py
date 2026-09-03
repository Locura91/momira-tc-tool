"""
cancellation_bulk_transport.py — bulk-change the cancellation policy on every (or a chosen
subset of) a supplier's already-live Transports, without going through per-item document
extraction.

WHY THIS EXISTS: cancellation_links.py already lets a saved link's tiers auto-fill a
document's missing cancellation terms AT EXTRACTION TIME - but that only ever helps the NEXT
create/update that happens to go through a document. It does nothing for the Transports a
supplier already has live today. Worse, build_transport_payloads deliberately LOCKS the
description field (where Transport's cancellation text lives, see below) on every ordinary
update/price-refresh, precisely so an unrelated pricing update can never silently reword it
(see that function's own "do not change the name and the description of transfer and
transport" comment). Changing a supplier's cancellation terms is therefore its own deliberate
action, never a side effect of anything else - this module is that action.

SCOPED TO TRANSPORT ONLY (CONFIRMED product owner, 2026-08-28): Transport has a real
structured `cancellationRanges` field a human can safely overwrite in isolation (see
schemas.ContractTransportCancellationRangeVO). Transfer has no such field - its cancellation
terms are baked as a sentence inside free-text voucher wording alongside pickup/what-to-bring/
manual-notes text with no reliable anchor to safely locate and replace, so it isn't offered
here.

WHERE THE CANCELLATION TEXT ACTUALLY LIVES ON A LIVE TRANSPORT (confirmed via real Swagger
data - see build_transport_payloads' own comment in builder.py): Transport's
ContractTransportDataSheetVO has no separate voucherRemarks field the way ClosedTour/Ticket/
Transfer do - the cancellation sentence is appended into `datasheets.EN.description` itself,
as one of several "<p>...</p>" paragraphs (service description first, then cancellation, then
optionally a "What to bring:" block, then optionally a manual note - see builder.py's
_with_what_to_bring/_with_manual_notes for that fixed ordering). Changing the STRUCTURED
cancellationRanges field alone would leave the customer-facing description text describing
the OLD policy, so this module rewrites both together - see _swap_cancellation_paragraph()
for how the right paragraph is found without disturbing anything else in the description
(what-to-bring, a manual note, even one that happens to mention "cancellation" itself - see
that function's docstring for why position, not just a keyword match, keeps this safe).

CONFIRMED SCOPE DECISIONS (product owner, 2026-08-28, AskUserQuestion):
  * Per-supplier only for now, not multi-supplier/all-at-once.
  * The new policy defaults from that supplier's saved Cancellation Link
    (cancellation_links.py, supplier-specific else company-wide) - or the house 30-day/free
    default when neither is saved - always editable by the human before applying.
  * EVERY live Transport is listed with its CURRENT policy shown - nothing "already filled
    out" differently is silently skipped or auto-detected as special; the human sees it
    (build_proposals' `unchanged` flag only affects the default checkbox state, never removes
    a row from the list) and decides per row.
  * The customer-facing description text is rewritten to match the new policy, not just the
    structured field.

Storage: none of its own - reads live Transports straight from Travel Compositor via the
passed-in client, and reads cancellation_links.py's existing store for the default. Nothing
here is cached between runs; every screen load re-fetches the live data fresh.
"""

# Stamped on every delivery - see platform_store.py's own header for why.
MODULE_BUILD = "2026-09-03-voucher-remarks-no-raw-supplier-cancellation-text"

import re
from typing import Any, Dict, List, Optional, Tuple

import cancellation_links
from builder import _cancellation_ranges_from_tiers, _cancellation_voucher_text, strip_stray_html
from state_store import StateStore

# CONFIRMED STANDING RULE (product owner, 2026-08-24, see builder.py's own copy of this
# comment): "if no specific policy is mentioned, leave the standardized Cancellation policy to
# 30 days or prior for 100% refund." Used here as the fallback default when a supplier has no
# saved Cancellation Link at all - matches schemas.ContractTransportCancellationRangeVO's own
# class default (days=30, percentage=100.0) exactly, just expressed in the fee-percentage
# shape this module (and cancellation_links.py) use everywhere else.
_HOUSE_DEFAULT_TIERS = [{"days": 30, "fee_percentage": 0.0}]

# [^>]* tolerates attributes on the tag itself (Travel Compositor's own editor writes
# "<p dir=\"ltr\">..." when a paragraph has been hand-edited there) - a bare r"<p>(.*?)</p>"
# would silently fail to match such a paragraph, sending _swap_cancellation_paragraph down its
# "no existing paragraph found" INSERT path instead of REPLACE, leaving the old cancellation
# sentence live in the description right next to the new one. CONFIRMED real via a 2026-08-30
# audit subagent + direct trace, not hypothetical - Travel Compositor's editor does emit
# attributed <p> tags.
_P_BLOCK_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.DOTALL | re.IGNORECASE)


def default_new_tiers(supplier_id: str) -> Tuple[List[Dict[str, Any]], str]:
    """Returns (tiers, source_label) to pre-fill the bulk-update form with - the supplier's
    saved Cancellation Link if one exists (supplier-specific wins over company-wide, same
    precedence as cancellation_links.resolve_cancellation_link), else the house 30-day/free
    default. Always just a starting point - the human can edit it before applying."""
    tiers, scope_label = cancellation_links.resolve_cancellation_link(supplier_id, "Transport")
    if tiers:
        return [dict(t) for t in tiers], scope_label
    return [dict(t) for t in _HOUSE_DEFAULT_TIERS], "the standing house default (30 days notice, full refund)"


def _wire_ranges_to_fee_tiers(cancellation_ranges) -> List[Dict[str, Any]]:
    """Converts a live Transport's raw cancellationRanges (Travel Compositor wire format: days
    + REFUND percentage) into the same {"days","fee_percentage"} shape cancellation_links.py
    and this module's own editable table use everywhere else - so a human comparing "current"
    against "new" in the review screen is comparing like units, not refund% against fee%."""
    out = []
    for r in (cancellation_ranges or []):
        if not isinstance(r, dict):
            continue
        days = r.get("days")
        refund_pct = r.get("percentage")
        if not isinstance(days, (int, float)) or not isinstance(refund_pct, (int, float)):
            continue
        fee_pct = max(0.0, min(100.0, 100.0 - float(refund_pct)))
        out.append({"days": int(days), "fee_percentage": fee_pct})
    return sorted(out, key=lambda t: t["days"], reverse=True)


def _tiers_equal(a, b) -> bool:
    """True when two tier lists describe the same policy, ignoring order and tiny float
    noise - used only to decide build_proposals' `unchanged` flag (the default checkbox
    state), never to hide or skip a row."""
    def _norm(tiers):
        return sorted((int(t.get("days", 0)), round(float(t.get("fee_percentage", 0) or 0), 4))
                      for t in (tiers or []) if isinstance(t, dict))
    return _norm(a) == _norm(b)


def _current_cancellation_snippet(description_html: str) -> Optional[str]:
    """The plain-text content of the FIRST "<p>...</p>" paragraph that mentions cancellation,
    or None if none does. Used only for display (the "current" side of the review screen) -
    see _swap_cancellation_paragraph for why "first match" is the safe choice, not just "any
    match"."""
    for m in _P_BLOCK_RE.finditer(description_html or ""):
        if "cancella" in m.group(1).lower():
            return strip_stray_html(m.group(1))
    return None


def _swap_cancellation_paragraph(description_html: str, new_text: str) -> Tuple[str, bool]:
    """Replaces the cancellation paragraph inside a live Transport's description HTML with
    `new_text`, leaving every other paragraph (the service description, an optional "What to
    bring:" block, an optional manual note) untouched. Returns (new_description_html,
    existing_paragraph_found).

    WHY "FIRST PARAGRAPH CONTAINING 'cancella'" IS THE SAFE MATCH, not just "any paragraph
    mentioning it": build_transport_payloads always composes description paragraphs in a FIXED
    order - service description, then the cancellation sentence (always present, see
    _cancellation_voucher_text's docstring - it's never actually blank), then optionally "What
    to bring:", then optionally a manual note LAST. A manual note can legitimately also mention
    cancellation (service_notes.py's own docstring gives "this supplier's cancellation terms
    changed in March" as an example note) - but because it is always appended after the real
    policy paragraph, taking the FIRST match rather than the last (or "any") reliably lands on
    the actual policy sentence, never a note that merely refers to it.

    WHEN NO PARAGRAPH MATCHES AT ALL (a record that predates this app's description shape, or
    was hand-edited in Travel Compositor): inserts the new paragraph right after the first
    existing one (assumed to be the service description), or as the whole description if there
    are no paragraphs at all. Returns False so the caller can flag this row for a human's
    double-check - an INSERT changes the document's structure more than a REPLACE does."""
    new_block = f"<p>{new_text}</p>"
    matches = list(_P_BLOCK_RE.finditer(description_html or ""))
    target = next((m for m in matches if "cancella" in m.group(1).lower()), None)
    if target is not None:
        new_html = description_html[:target.start()] + new_block + description_html[target.end():]
        return new_html, True
    if matches:
        insert_at = matches[0].end()
        new_html = description_html[:insert_at] + new_block + description_html[insert_at:]
    else:
        new_html = new_block + (description_html or "")
    return new_html, False


def load_supplier_transports_for_cancellation(client, supplier_id: str
                                              ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Every live Transport this supplier has, with its current cancellation policy. Unlike
    price_refresh.load_supplier_transports, this never fetches per-transport Options (they
    have no cancellation data of their own - cancellationRanges lives only on the parent
    record) - one GET /transport/{supplierId} call covers everything this screen needs."""
    try:
        data = client.get_transports(supplier_id)
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"
    if isinstance(data, dict) and "error" in data:
        return [], str(data.get("message") or data.get("error"))
    records = data.get("transport", []) if isinstance(data, dict) else (data or [])
    records = [r for r in records if isinstance(r, dict)]

    rows = []
    for record in records:
        datasheet_en = ((record.get("datasheets") or {}).get("EN")) or {}
        description_html = datasheet_en.get("description") or ""
        segment = (record.get("segments") or [{}])[0] if isinstance(record.get("segments"), list) else {}
        rows.append({
            "id": record.get("id"),
            "name": record.get("name") or "",
            "departure_code": segment.get("departureLocationCode") or "",
            "arrival_code": segment.get("arrivalLocationCode") or "",
            "current_fee_tiers": _wire_ranges_to_fee_tiers(record.get("cancellationRanges")),
            "current_cancellation_snippet": _current_cancellation_snippet(description_html),
            "description_html": description_html,
            "raw": record,
        })
    return sorted(rows, key=lambda r: (r["name"] or "").lower()), None


def build_proposals(rows: List[Dict[str, Any]], new_tiers) -> List[Dict[str, Any]]:
    """Builds one old->new proposal per row for the review screen. `new_tiers` is the same
    editable {"days","fee_percentage"} shape as cancellation_links.py's tables - cleaned and
    floored (via builder._cancellation_ranges_from_tiers, the SAME floor every other product
    already applies - a document/human asking for full refund on shorter notice than 30 days
    still gets pushed out to 30) exactly once here, then applied identically to every row."""
    clean_new = cancellation_links._clean_tiers(new_tiers)
    new_ranges = _cancellation_ranges_from_tiers(clean_new) or [(30, 100.0)]
    new_wire = [{"days": d, "percentage": p, "isBeforeStart": True} for d, p in new_ranges]
    new_text = _cancellation_voucher_text(None, new_ranges)
    # What actually gets APPLIED (and so what the human should be shown and what `unchanged`
    # must compare against) is new_wire after the 30-day/100%-refund floor above - not the raw
    # clean_new the human typed. cancellation_links.set_supplier_link/set_type_link do not
    # enforce that floor at save time (only _clean_tiers runs there), so a saved link can read
    # e.g. {days:14, fee_percentage:0} even though applying it here always floors to
    # {days:30, fee_percentage:0}. Comparing `unchanged` against the pre-floor clean_new could
    # mark a row unchanged (and default its checkbox off) when applying the policy would in
    # fact change the live record. CONFIRMED real via a 2026-08-30 audit subagent + direct
    # trace of cancellation_links.py's save path.
    new_fee_tiers = _wire_ranges_to_fee_tiers(new_wire)

    proposals = []
    for row in rows:
        new_description_html, existing_found = _swap_cancellation_paragraph(row["description_html"], new_text)
        unchanged = (
            _tiers_equal(row["current_fee_tiers"], new_fee_tiers)
            and (row["current_cancellation_snippet"] or "").strip() == new_text.strip()
        )
        proposals.append({
            "id": row["id"],
            "name": row["name"],
            "departure_code": row["departure_code"],
            "arrival_code": row["arrival_code"],
            "current_fee_tiers": row["current_fee_tiers"],
            "current_cancellation_snippet": row["current_cancellation_snippet"],
            "new_fee_tiers": new_fee_tiers,
            "new_cancellation_text": new_text,
            "new_ranges_wire": new_wire,
            "new_description_html": new_description_html,
            "existing_paragraph_found": existing_found,
            "unchanged": unchanged,
            "raw": row["raw"],
        })
    return proposals


def apply_proposals(client, supplier_id: str, proposals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Applies every proposal passed in - the caller filters to only the accepted/checked rows
    first, same convention as price_refresh.apply_proposals and the supplier-migration flow's
    own selected-indices filtering.

    Each write is the record's own live GET shape, PUT back whole with cancellationRanges and
    EVERY datasheet's description changed - see the CONFIRMED BUG FIX comment below for why
    this now touches every language, not only EN. Nothing else on the record - pricing,
    segments, images, dates - is touched.

    CONFIRMED BUG FIX (full-app audit HIGH, 2026-09-01): this used to rewrite ONLY the EN
    datasheet's description, by design (see this module's own docstring: cancellationRanges is
    the safely-editable structured field, and only EN's free text was meant to change here).
    That was correct for the STRUCTURED field, but it left every OTHER language's datasheet
    describing the OLD policy in prose - and the translation tracker (state_store.py's
    verify_and_filter_needed, the "check state and verify existing content" self-healing check)
    then permanently froze that stale text: on the next regular sync, it sees the non-EN
    description no longer matches the (now-changed) EN source and reads that mismatch as "this
    is already a genuine, up-to-date translation the tracker just lost track of" - exactly the
    signal it's designed to trust, since that's normally how a state-tracking gap looks. It has
    no way to tell that apart from "this text is stale because EN just changed outside the
    normal sync flow," so it marks the stale foreign text as done and never revisits it. Real
    consequence: non-English customers keep reading a cancellation policy the company no longer
    honors, indefinitely.

    Fix, two parts: (1) the same new cancellation sentence (English, until the next real
    translation pass) is swapped into every OTHER language's datasheet too, via the identical
    _swap_cancellation_paragraph helper used for EN - so no customer, in any language, is ever
    shown a stale, no-longer-honored policy, even before it's properly translated. (2) the
    translation-tracker state for this Transport is explicitly cleared (state_store.clear_state)
    so the next regular sync treats it as never-synced instead of running the self-healing check
    against content this tool just knowingly went around."""
    results = []
    for p in proposals:
        updated = dict(p["raw"])
        updated["cancellationRanges"] = p["new_ranges_wire"]
        datasheets = dict(updated.get("datasheets") or {})
        new_text = p["new_cancellation_text"]
        for lang, sheet in datasheets.items():
            sheet = dict(sheet or {})
            new_html, _found = _swap_cancellation_paragraph(sheet.get("description") or "", new_text)
            sheet["description"] = new_html
            datasheets[lang] = sheet
        if "EN" not in datasheets:
            datasheets["EN"] = {"description": p["new_description_html"]}
        updated["datasheets"] = datasheets
        try:
            result = client.update_transport(supplier_id, updated)
        except Exception as e:
            results.append({"id": p["id"], "name": p["name"], "ok": False,
                            "detail": f"{type(e).__name__}: {e}"})
            continue
        if isinstance(result, dict) and "error" in result:
            results.append({"id": p["id"], "name": p["name"], "ok": False,
                            "detail": str(result.get("message") or result.get("error"))})
        else:
            try:
                StateStore().clear_state("transport", supplier_id, p["id"])
            except Exception:
                pass  # best-effort - a failed cache-invalidation must never undo a real, already-applied publish
            results.append({"id": p["id"], "name": p["name"], "ok": True, "detail": ""})
    return results
