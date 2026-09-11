"""
cancellation_bulk.py — bulk-change the cancellation policy on every (or a chosen subset of) a
supplier's already-live ClosedTour, Ticket, Transfer, or Hotel services, without going through
per-item document extraction.

CONFIRMED REQUEST (product owner, 2026-09-10): "bulk update cancellation policy --> this must
be usable for all Services: Hotel; Transfer, Transport, Ticket and ClosedTour - so far it looks
like only Transport can do it." cancellation_bulk_transport.py already covers Transport (built
2026-08-28) - this module is the other four. Transport's own module is left untouched: its
cancellation text lives inside HTML "<p>...</p>" paragraphs in `description` (Transport has no
separate voucherRemarks-style field until the 2026-09-10 schema fix, and even after that fix
its cancellation sentence is still deliberately kept in `description` - see builder.py's own
comment there), a genuinely different storage shape from the plain-text voucherRemarks blocks
these four product types use - see WHERE THE TEXT LIVES below.

WHY THIS EXISTS (same reasoning as cancellation_bulk_transport.py): cancellation_links.py lets
a saved link's tiers auto-fill a document's missing cancellation terms AT EXTRACTION TIME, but
that only ever helps the NEXT create/update that happens to go through a document - it does
nothing for services a supplier already has live today. Changing a supplier's cancellation
terms is therefore its own deliberate action, never a side effect of anything else.

STRUCTURED FIELD, where one genuinely exists: ClosedTour (schemas.CancellationRange:
days/percentage) and Ticket (schemas.TicketCancellationRange: cancellationDays/
cancellationPercentage, DIFFERENT field names - confirmed via real data, see that class's own
docstring) both carry a real structured cancellationRanges field on the main record, rewritten
here alongside the text - same "both together, never just one" rule
cancellation_bulk_transport.py already established (leaving the structured field alone while
only the text changes would tell the customer one policy while Travel Compositor enforces
another). Transfer and Hotel have NO structured cancellation field at all (confirmed - see
schemas.py's own notes on TransferDescriptorVO and the Hotel contract VO) - text only for
those two; _STRUCTURED_TIER_FIELDS below is the single source of truth for which three types
get the structured rewrite.

RE-CONFIRMED WITH LIVE EVIDENCE (product owner, 2026-09-11): after this tool reported "18
updated" for a Transfer bulk run, the product owner checked Travel Compositor's own
Cancellation tab for one of those Transfers and found it still empty, and pulled a real
GET .../transfer/{supplierId}/{transferId} before and after manually setting "30 days or
prior" in THAT admin screen and clicking Save there - the two GET responses were byte-for-byte
identical, no cancellation-shaped field appeared anywhere. So Travel Compositor's own admin UI
Cancellation tab for Transfer isn't wired to this API resource either - it isn't only this
tool that can't write it. Contrast confirmed the same way for Transport on the same day: a
before/after GET on a real Transport showed `cancellationRanges` genuinely appear after being
set - so this isn't a general "the API is unreliable" finding, just a real, confirmed gap
specific to Transfer (and, per the same original schemas.py reasoning, presumably Hotel,
though that one hasn't had its own live before/after test yet). See
claude/transfer-cancellation-no-structured-field-2026-09-11.md (project docs) for the full
before/after JSON. render_generic_cancellation_bulk_flow (app.py) now shows an explicit info
banner on the Result screen for these two product types so "N updated" is never misread as
"the structured field changed too."

WHERE THE TEXT LIVES: all four store their cancellation sentence as one of several PLAIN-TEXT
blocks in voucherRemarks, separated by a blank line ("\\n\\n") - NOT HTML <p> tags the way
Transport's description is (see builder.py's _cancellation_voucher_text/_with_what_to_bring/
_with_manual_notes: every one of these four builders composes cancellation text first, then
optionally appends a what-to-bring block, then optionally a manual note last - plain text
throughout). ClosedTour/Ticket/Transfer store voucherRemarks per-language under
record["datasheets"][lang]["voucherRemarks"] (bulk_notes.SHAPE_DATASHEETS); Hotel stores it as
its own top-level list of {"language","description"} entries instead
(bulk_notes.SHAPE_TRANSLATION_LIST). _text_entries/_set_text_entries below abstract over both
shapes so the rest of this module never has to care which one it's looking at.
_swap_cancellation_block is the plain-text equivalent of
cancellation_bulk_transport._swap_cancellation_paragraph - same "first block mentioning
'cancella' is the real policy sentence, never a later manual note that merely refers to it"
reasoning, just operating on blank-line-separated text instead of HTML paragraphs.

EVERY LANGUAGE PRESENT gets the SAME new English text swapped in, not just EN - identical
reasoning to cancellation_bulk_transport.apply_proposals' own confirmed bug fix (a stale
foreign-language cancellation sentence must never be left live after the policy changes, even
before a proper translation pass reaches it) - and each record's translation-tracker state is
cleared afterward for the same reason.

Reuses bulk_notes.py's existing PRODUCTS config (list/fetch/update function names, per-type
storage shape, id field) and list_services (which already handles ClosedTour's no-list-endpoint/
manual-codes case, and Hotel's list-returns-summaries-only case) rather than duplicating any of
that plumbing - this module only adds the cancellation-specific logic on top.

CONFIRMED SCOPE DECISIONS (mirrors cancellation_bulk_transport.py exactly, same conversation
thread, 2026-08-28 -> 2026-09-10):
  * Per-supplier only for now, not multi-supplier/all-at-once (ClosedTour has no supplier-wide
    listing anyway, so it always works per pasted code(s)).
  * The new policy defaults from that supplier's saved Cancellation Link
    (cancellation_links.py, supplier-specific else company-wide) - or the house 30-day/free
    default when neither is saved - always editable by the human before applying.
  * EVERY matching service is listed with its CURRENT policy shown - nothing "already filled
    out" differently is silently skipped; `unchanged` only affects the default checkbox state
    in the review UI, never removes a row from the list.
  * The customer-facing text is rewritten to match the new policy, not just the structured
    field (where one exists).

Storage: none of its own - reads live services straight from Travel Compositor via the passed-
in client, and reads cancellation_links.py's existing store for the default. Nothing here is
cached between runs; every screen load re-fetches the live data fresh.
"""

# Stamped on every delivery - see platform_store.py's own header for why.
MODULE_BUILD = "2026-09-11-hotel-offer-supplement-rate-gaps"

import copy
from typing import Any, Dict, List, Optional, Tuple

import bulk_notes
import builder
import cancellation_links
from state_store import StateStore

# CONFIRMED STANDING RULE (product owner, 2026-08-24, see builder.py's own copy of this
# comment): "if no specific policy is mentioned, leave the standardized Cancellation policy to
# 30 days or prior for 100% refund." Used here as the fallback default when a supplier has no
# saved Cancellation Link at all - identical constant to cancellation_bulk_transport.py's own.
_HOUSE_DEFAULT_TIERS = [{"days": 30, "fee_percentage": 0.0}]

# Which product types have a genuine structured cancellationRanges field, and what its two
# per-entry keys are actually called on the wire (CONFIRMED DIFFERENT for Ticket - see
# schemas.TicketCancellationRange's own docstring). Transfer and Hotel are deliberately absent -
# see this module's own docstring for why.
_STRUCTURED_TIER_FIELDS: Dict[str, Tuple[str, str]] = {
    "ClosedTour": ("days", "percentage"),
    "Ticket": ("cancellationDays", "cancellationPercentage"),
}

# state_store.py's own entity_type strings, confirmed via every sync_*.py module's real call
# sites (sync_transfer.py/sync_hotel.py/sync_ticket.py/sync_closed_tour.py) - deliberately NOT
# just product_type.lower(), since ClosedTour's is "closed_tour" (with an underscore), not
# "closedtour".
_STATE_ENTITY_TYPE: Dict[str, str] = {
    "ClosedTour": "closed_tour", "Ticket": "ticket", "Transfer": "transfer", "Hotel": "hotel",
}

PRODUCT_TYPES = ("ClosedTour", "Ticket", "Transfer", "Hotel")


def default_new_tiers(supplier_id: str, product_type: str) -> Tuple[List[Dict[str, Any]], str]:
    """Returns (tiers, source_label) to pre-fill the bulk-update form with - the supplier's
    saved Cancellation Link if one exists (supplier-specific wins over company-wide), else the
    house 30-day/free default. Always just a starting point - the human can edit it before
    applying. Identical logic to cancellation_bulk_transport.default_new_tiers, just generic
    over product_type instead of hardcoded to "Transport"."""
    tiers, scope_label = cancellation_links.resolve_cancellation_link(supplier_id, product_type)
    if tiers:
        return [dict(t) for t in tiers], scope_label
    return [dict(t) for t in _HOUSE_DEFAULT_TIERS], "the standing house default (30 days notice, full refund)"


def _wire_ranges_to_fee_tiers(cancellation_ranges, days_key: str, pct_key: str) -> List[Dict[str, Any]]:
    """Converts a live record's raw cancellationRanges (days/percentage under whichever two key
    names this product type actually uses) into the same {"days","fee_percentage"} shape
    cancellation_links.py and this module's own editable table use everywhere else."""
    out = []
    for r in (cancellation_ranges or []):
        if not isinstance(r, dict):
            continue
        days = r.get(days_key)
        refund_pct = r.get(pct_key)
        if not isinstance(days, (int, float)) or not isinstance(refund_pct, (int, float)):
            continue
        fee_pct = max(0.0, min(100.0, 100.0 - float(refund_pct)))
        out.append({"days": int(days), "fee_percentage": fee_pct})
    return sorted(out, key=lambda t: t["days"], reverse=True)


def _tiers_equal(a, b) -> bool:
    """True when two tier lists describe the same policy, ignoring order and tiny float
    noise - used only to decide build_proposals' `unchanged` flag, never to hide or skip a
    row. `a`/`b` may be None (a product type with no structured field), which compares equal
    to itself only - callers only reach this when both sides genuinely have tiers to compare."""
    def _norm(tiers):
        return sorted((int(t.get("days", 0)), round(float(t.get("fee_percentage", 0) or 0), 4))
                      for t in (tiers or []) if isinstance(t, dict))
    return _norm(a) == _norm(b)


def _current_cancellation_snippet(text: Optional[str]) -> Optional[str]:
    """The plain-text content of the FIRST blank-line-separated block that mentions
    cancellation, or None if none does. Used only for display (the "current" side of the
    review screen) - see _swap_cancellation_block for why "first match" is the safe choice."""
    for block in (text or "").split("\n\n"):
        if "cancella" in block.lower():
            return block.strip()
    return None


def _swap_cancellation_block(text: Optional[str], new_text: str) -> Tuple[str, bool]:
    """Plain-text equivalent of cancellation_bulk_transport._swap_cancellation_paragraph, for
    these four product types' blank-line-separated voucherRemarks blocks (never HTML <p>
    paragraphs - see this module's own docstring). Returns (new_text_field, existing_block_found).

    WHY "FIRST BLOCK CONTAINING 'cancella'" IS THE SAFE MATCH: every one of these four
    builders composes the cancellation sentence FIRST (see builder.py's own call sites -
    _cancellation_voucher_text always runs before _with_what_to_bring/_with_manual_notes are
    layered on top), so the first matching block reliably lands on the real policy sentence,
    never a later manual note that merely refers to cancellation (service_notes.py's own
    docstring gives exactly that example).

    WHEN NO BLOCK MATCHES AT ALL (a record that predates this app's voucherRemarks convention,
    or was hand-edited in Travel Compositor): the new block is PREPENDED rather than inserted
    after an assumed "first paragraph" the way Transport's HTML swap does - these four fields
    don't necessarily start with an unrelated description block the way Transport's
    `description` does; cancellation text is always meant to be first here. Returns False so
    the caller can flag this row."""
    text = text or ""
    blocks = [b for b in text.split("\n\n")] if text else []
    target_idx = next((i for i, b in enumerate(blocks) if "cancella" in b.lower()), None)
    if target_idx is not None:
        blocks[target_idx] = new_text
        return "\n\n".join(b for b in blocks if b.strip()), True
    new_blocks = [new_text] + [b for b in blocks if b.strip()]
    return "\n\n".join(new_blocks), False


def _text_entries(record: Dict[str, Any], shape: str) -> List[Tuple[str, str]]:
    """Every (language, current_voucherRemarks_text) pair on this record, regardless of which
    of the two storage shapes this product type uses."""
    if shape == bulk_notes.SHAPE_DATASHEETS:
        sheets = record.get("datasheets") or {}
        return [(lang, (sheet or {}).get("voucherRemarks") or "")
                for lang, sheet in sheets.items() if isinstance(sheet, dict)]
    entries = record.get("voucherRemarks") or []
    return [(e.get("language") or "EN", e.get("description") or "")
            for e in entries if isinstance(e, dict)]


def _set_text_entries(record: Dict[str, Any], shape: str, new_by_lang: Dict[str, str]) -> None:
    """Writes `new_by_lang` ({language: new_text}) into `record` IN PLACE, matching whichever
    storage shape this product type uses. A language present in new_by_lang but not yet on the
    record (only ever EN, since this app composes just that one) is appended fresh - same
    "if EN missing, add it" fallback cancellation_bulk_transport.apply_proposals already uses."""
    if shape == bulk_notes.SHAPE_DATASHEETS:
        sheets = record.get("datasheets")
        if not isinstance(sheets, dict):
            return
        for lang, sheet in sheets.items():
            if isinstance(sheet, dict) and lang in new_by_lang:
                sheet["voucherRemarks"] = new_by_lang[lang]
        if not sheets and "EN" in new_by_lang:
            record["datasheets"] = {"EN": {"voucherRemarks": new_by_lang["EN"]}}
        return
    entries = record.get("voucherRemarks")
    if not isinstance(entries, list):
        entries = []
        record["voucherRemarks"] = entries
    seen = set()
    for e in entries:
        if isinstance(e, dict):
            lang = e.get("language") or "EN"
            if lang in new_by_lang:
                e["description"] = new_by_lang[lang]
                seen.add(lang)
    for lang, text in new_by_lang.items():
        if lang not in seen:
            entries.append({"language": lang, "description": text})


def load_supplier_services_for_cancellation(
        client, supplier_id: str, product_type: str,
        codes: Optional[List[str]] = None) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Every matching service of this supplier and type, with its current cancellation policy -
    the generic-over-product-type equivalent of
    cancellation_bulk_transport.load_supplier_transports_for_cancellation. Reuses
    bulk_notes.list_services (handles ClosedTour's manual-codes requirement) and, for a product
    type whose list endpoint returns summaries only (Hotel), fetches each one in full - the
    same pattern bulk_notes.plan()'s own loop already uses for that case."""
    cfg = bulk_notes.PRODUCTS.get(product_type)
    if not cfg:
        return [], f"Unknown product type {product_type!r}"
    summaries, err = bulk_notes.list_services(client, supplier_id, product_type, codes=codes)
    if err and not summaries:
        return [], err

    shape = cfg["shape"]
    tier_fields = _STRUCTURED_TIER_FIELDS.get(product_type)
    failures = [err] if err else []
    rows = []
    for summary in summaries:
        ident = summary.get(cfg["id_field"])
        record = summary
        if not cfg["full_in_list"]:
            try:
                record = getattr(client, cfg["fetch_fn"])(supplier_id, ident)
            except Exception as e:
                failures.append(f"{ident} ({type(e).__name__}: {e})")
                continue
            if not isinstance(record, dict) or "error" in record:
                detail = (str(record.get("message") or record.get("error"))
                          if isinstance(record, dict) else
                          f"unexpected response type {type(record).__name__}")
                failures.append(f"{ident} ({detail})")
                continue
        entries = _text_entries(record, shape)
        en_text = next((t for lang, t in entries if lang == "EN"), entries[0][1] if entries else "")
        current_fee_tiers = (
            _wire_ranges_to_fee_tiers(record.get("cancellationRanges"), *tier_fields)
            if tier_fields else None
        )
        rows.append({
            "id": ident, "name": bulk_notes.label_for(record, product_type),
            "current_fee_tiers": current_fee_tiers,
            "current_cancellation_snippet": _current_cancellation_snippet(en_text),
            "raw": record,
        })
    combined_err = "; ".join(failures) if failures else None
    return sorted(rows, key=lambda r: (r["name"] or "").lower()), combined_err


def build_proposals(rows: List[Dict[str, Any]], new_tiers, product_type: str) -> List[Dict[str, Any]]:
    """Builds one old->new proposal per row for the review screen - the generic-over-
    product-type equivalent of cancellation_bulk_transport.build_proposals. `new_tiers` is the
    same editable {"days","fee_percentage"} shape cancellation_links.py's tables use; cleaned
    and floored (via builder._cancellation_ranges_from_tiers, the same 30-day/100%-refund floor
    every product already applies) exactly once here, then applied identically to every row."""
    clean_new = cancellation_links._clean_tiers(new_tiers)
    new_ranges = builder._cancellation_ranges_from_tiers(clean_new) or [(30, 100.0)]
    new_text = builder._cancellation_voucher_text(None, new_ranges)

    tier_fields = _STRUCTURED_TIER_FIELDS.get(product_type)
    new_wire = None
    new_fee_tiers = None
    if tier_fields:
        days_key, pct_key = tier_fields
        new_wire = [{days_key: d, pct_key: p, "isBeforeStart": True} for d, p in new_ranges]
        new_fee_tiers = _wire_ranges_to_fee_tiers(new_wire, days_key, pct_key)

    shape = bulk_notes.PRODUCTS[product_type]["shape"]
    proposals = []
    for row in rows:
        record = row["raw"]
        entries = _text_entries(record, shape)
        new_by_lang: Dict[str, str] = {}
        any_found = False
        for lang, text in entries:
            swapped, found = _swap_cancellation_block(text, new_text)
            new_by_lang[lang] = swapped
            any_found = any_found or found
        if not entries:
            new_by_lang["EN"] = new_text

        unchanged = (
            (tier_fields is None or _tiers_equal(row["current_fee_tiers"], new_fee_tiers))
            and (row["current_cancellation_snippet"] or "").strip() == new_text.strip()
        )
        # CONFIRMED REAL RULE (product owner, 2026-09-11, extended same day): "the app shall not
        # overwrite any cancellation, if there is an existing cancellation included, which is
        # more strict" - MUST cover ALL FIVE product types ("the cancellation policy is
        # required to ALL services: Ticket, ClosedTour and Hotels too. But most likely
        # ClosedTours and Hotels have a more strict policy"). See
        # builder.existing_cancellation_at_least_as_strict's own docstring for the comparison
        # rule and worked examples.
        #
        # ClosedTour/Ticket (tier_fields is not None) compare the real structured field, same
        # as cancellation_bulk_transport.build_proposals. Transfer/Hotel have NO structured
        # field at all (this module's own docstring) - their only record of the current policy
        # is the free-text voucher snippet, so builder.parse_cancellation_tiers_from_voucher_
        # text is used to read it back into tiers FIRST, and existing_stricter is computed from
        # that only when the parse succeeds. A snippet that doesn't match a shape this app's own
        # synthesizer could have written (a supplier's own wording, or hand-edited text) parses
        # to None - existing_unparseable is set instead, and the row is left unselected by
        # default (never silently overwritten OR silently skipped) so a human decides.
        existing_stricter = False
        existing_unparseable = False
        if not unchanged:
            if tier_fields:
                current_ranges = [(t["days"], 100.0 - t["fee_percentage"]) for t in (row["current_fee_tiers"] or [])]
                existing_stricter = builder.existing_cancellation_at_least_as_strict(current_ranges, new_ranges)
            else:
                parsed = builder.parse_cancellation_tiers_from_voucher_text(row["current_cancellation_snippet"])
                if parsed is None:
                    existing_unparseable = bool((row["current_cancellation_snippet"] or "").strip())
                else:
                    existing_stricter = builder.existing_cancellation_at_least_as_strict(parsed, new_ranges)
        proposals.append({
            "id": row["id"], "name": row["name"],
            "current_fee_tiers": row["current_fee_tiers"],
            "current_cancellation_snippet": row["current_cancellation_snippet"],
            "new_fee_tiers": new_fee_tiers,
            "new_cancellation_text": new_text,
            "new_ranges_wire": new_wire,
            "new_text_by_lang": new_by_lang,
            "existing_paragraph_found": any_found,
            "unchanged": unchanged,
            "existing_stricter": existing_stricter,
            "existing_unparseable": existing_unparseable,
            "raw": record,
        })
    return proposals


def apply_proposals(client, supplier_id: str, product_type: str,
                    proposals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Applies every proposal passed in - the caller filters to only the accepted/checked rows
    first, same convention as cancellation_bulk_transport.apply_proposals. Each write is the
    record's own live GET shape, PUT back whole with cancellationRanges (where this product
    type has one) and every language's voucherRemarks changed. Nothing else on the record is
    touched. Clears this record's translation-tracker state afterward, same confirmed bug fix
    as cancellation_bulk_transport.apply_proposals (a stale foreign-language sentence must
    never survive un-flagged after the policy changes)."""
    cfg = bulk_notes.PRODUCTS[product_type]
    update_fn = getattr(client, cfg["update_fn"])
    shape = cfg["shape"]
    tier_fields = _STRUCTURED_TIER_FIELDS.get(product_type)
    entity_type = _STATE_ENTITY_TYPE.get(product_type)

    results = []
    for p in proposals:
        # BELT AND SUSPENDERS (product owner, 2026-09-11 "do not overwrite a stricter existing
        # policy" rule) - same re-check as cancellation_bulk_transport.apply_proposals' own
        # identical guard: the review UI already disables/unchecks a row flagged
        # existing_stricter=True, but this is the actual live-write boundary.
        if p.get("existing_stricter"):
            results.append({"id": p["id"], "name": p["name"], "ok": True, "skipped": True,
                            "detail": "left unchanged - existing policy is already at least as strict"})
            continue
        updated = copy.deepcopy(p["raw"])
        bulk_notes._normalize_for_put(updated, product_type)
        if tier_fields and p.get("new_ranges_wire") is not None:
            updated["cancellationRanges"] = p["new_ranges_wire"]
        _set_text_entries(updated, shape, p["new_text_by_lang"])
        try:
            result = update_fn(supplier_id, updated)
        except Exception as e:
            results.append({"id": p["id"], "name": p["name"], "ok": False,
                            "detail": f"{type(e).__name__}: {e}"})
            continue
        if isinstance(result, dict) and "error" in result:
            results.append({"id": p["id"], "name": p["name"], "ok": False,
                            "detail": str(result.get("message") or result.get("error"))})
        else:
            if entity_type:
                try:
                    StateStore().clear_state(entity_type, supplier_id, str(p["id"]))
                except Exception:
                    pass  # best-effort - a failed cache-invalidation must never undo a real publish
            results.append({"id": p["id"], "name": p["name"], "ok": True, "detail": ""})
    return results
