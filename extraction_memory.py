"""
extraction_memory.py — the platform learns from the corrections a human makes.

THE PROBLEM: every contract is read from scratch. When the extractor misreads something
and a human fixes it on the review screen — a pickup point written the way the supplier
writes it rather than the way Travel Compositor needs it, a vehicle called "Car" that
should be "Sedan", a cancellation policy the document never states and the operator types
in every single time — that correction is thrown away the moment the service is published.
The next contract from the same supplier makes the identical mistake, and a person fixes
it again. Nothing gets better, no matter how many contracts go through.

WHAT IS ACTUALLY LEARNED: one very specific, defensible thing —

    "for supplier X, product type Y, whenever the extractor produces value V in field F,
     a human changes it to W."

That is a value mapping, not a guess about meaning. It is recorded per supplier because
suppliers are the unit that has a consistent house style: Masons Travel always writes
"Airport" where the system needs "Seychelles International Airport", and that fact says
nothing about any other supplier. It is recorded per product type because the same word
can mean different things on a transfer and on a hotel.

WHY MAPPINGS AND NOT SOMETHING CLEVERER: a mapping can be shown to a person in one line
("you have changed this 3 times before"), audited, and deleted. It cannot silently drift.
An operator can look at the list and immediately tell whether it is right. That property
matters more here than sophistication, because these corrections flow into live inventory
that customers book.

WHAT IS DELIBERATELY *NOT* AUTO-APPLIED (see _apply_blocked): dates and prices. A season
date or a price repeats across every service in one contract, so a mapping learned from it
looks extremely confident after a single document — and then fires on next season's
contract, where it is not just wrong but wrong in a way that costs money and is hard to
spot on a review screen. Those corrections are still RECORDED and visible in the memory
panel, so a person can see the pattern; they are never pre-filled.

CONFIRMATION THRESHOLD: a mapping is applied only after it has been seen on
_APPLY_AFTER separate publishes. One correction is as likely to be a typo or a one-off as
a rule; two is a pattern. Everything applied is marked on screen and a human still confirms
it before publishing, so the failure mode of a bad lesson is a person noticing a pre-filled
value is wrong — not bad data reaching Travel Compositor.
"""

# Stamped on every delivery. app.py compares this against its own build string and says
# so on screen when they differ - a partial push (one file committed, another not) used to
# surface only as a traceback whose line numbers pointed at unrelated code.
MODULE_BUILD = "2026-08-30-hotel-matching-fixes"

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import platform_store

_NAMESPACE = "extraction_memory"

# Where prune_old_mappings() records when it last ran, for the memory panel. Deliberately
# separate from _NAMESPACE so a prune-meta row can never be mistaken for a supplier row by
# anything that iterates get_namespace(_NAMESPACE) (list_learned, prune_old_mappings itself).
_PRUNE_META_NAMESPACE = "extraction_memory_meta"
_PRUNE_META_KEY = "last_prune"

# How many separate publishes must show the same correction before it is pre-filled.
_APPLY_AFTER = 2

# Values longer than this are recorded but never used as a lookup key - they are unlikely
# to recur character-for-character and would bloat the per-supplier row.
_MAX_VALUE_CHARS = 600

# Per (supplier, product type, field), keep at most this many distinct mappings; the
# least-recently-seen is dropped. Stops a field whose value is unique per service (a
# service name, say) from growing without limit.
_MAX_MAPPINGS_PER_FIELD = 40

# Fields never learned at all. Notes have their own deliberate mechanism in
# service_notes.py - learning them here would apply a one-off remark to every future
# service, which is exactly the thing standing notes exist to make an explicit choice.
_NEVER_LEARN = {
    "manual_notes", "one_off_note",
    # Internal bookkeeping that happens to ride along in the same dict.
    "_raw", "_learned_applied", "publish_status", "build_result",
    "existing_snapshot", "existing_snapshot_id", "confirmed_existing_id",
}

# Recorded, shown in the memory panel, but never pre-filled. See the module docstring.
_APPLY_BLOCKED_PATTERNS = (
    "date", "price", "amount", "cost", "fee", "rate", "tariff", "supplement",
    "time",          # departure_time/arrival_time: an extractor default, see below
)

# CONFIRMED REAL BUG (audit): the block list was name-based only, and the extractor emits a
# FIXED DEFAULT for these when a document doesn't state them - every transfer comes out
# charge_unit="per_pax", currency="EUR", is_zone_based=False, min/max_occupancy=1/4, and
# every transport departure_time="09:00:00". Because the default is byte-identical in every
# document, two corrections in a row look like a confident supplier-wide rule and get
# applied to the next document, which may genuinely be per-pax, or genuinely EUR, or
# genuinely depart at 09:00. charge_unit decides whether a price is multiplied by headcount,
# so this is a money error of exactly the class the price block exists to prevent. They are
# still RECORDED and visible - just never auto-filled.
_APPLY_BLOCKED_FIELDS = {
    "charge_unit", "currency", "is_zone_based", "min_occupancy", "max_occupancy",
    "plus_days", "price_type", "min_pax", "max_pax",
}


def _apply_blocked(field: str) -> bool:
    f = field.lower()
    if f in _APPLY_BLOCKED_FIELDS:
        return True
    return any(p in f for p in _APPLY_BLOCKED_PATTERNS)


def _key(supplier_id: str, product_type: str) -> str:
    return f"{supplier_id}|{product_type}"


def _norm(value: Any) -> Optional[str]:
    """The lookup key for a value. Case- and whitespace-insensitive, because a supplier
    writing "HURGHADA  AIRPORT" and "Hurghada Airport" in two contracts is the same fact.

    Returns None for anything that must not be learned: lists and dicts (a correction to a
    price table is specific to one service and generalises to nothing), and values too long
    to plausibly recur."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        # 22 and 22.0 are the same correction.
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    if isinstance(value, str):
        if len(value) > _MAX_VALUE_CHARS:
            return None
        return re.sub(r"\s+", " ", value).strip().lower()
    return None  # list / dict / anything else


def _load(supplier_id: str, product_type: str) -> Dict[str, Any]:
    row = platform_store.get(_NAMESPACE, _key(str(supplier_id), product_type)) or {}
    if not isinstance(row, dict) or "fields" not in row:
        return {"fields": {}}
    return row


def _save(supplier_id: str, product_type: str, row: Dict[str, Any]) -> bool:
    row["updated_at"] = datetime.now(timezone.utc).isoformat()
    return platform_store.set(_NAMESPACE, _key(str(supplier_id), product_type), row)


def _prune(mappings: Dict[str, Any]) -> Dict[str, Any]:
    """Keep the most useful mappings when a field has accumulated too many: highest
    confirmation count first, then most recently seen. Dropping the rarely-confirmed ones
    is right - they are the ones least likely to be a real rule."""
    if len(mappings) <= _MAX_MAPPINGS_PER_FIELD:
        return mappings
    ranked = sorted(mappings.items(),
                    key=lambda kv: (kv[1].get("count", 0), kv[1].get("last_seen", "")),
                    reverse=True)
    return dict(ranked[:_MAX_MAPPINGS_PER_FIELD])


def record_corrections(supplier_id: str, product_type: str,
                       extracted: Dict[str, Any], final: Dict[str, Any],
                       service_label: str = "") -> List[Dict[str, Any]]:
    """Compare what the extractor produced against what the human actually published, and
    remember every difference as a mapping.

    Call this ONCE, on a SUCCESSFUL publish. Not while editing: a half-typed value is not a
    correction, and not on a failed publish either, because nobody has yet agreed that the
    value was right. `extracted` must be the raw extractor output taken before any learned
    mapping was applied - see snapshot(). Recording against the already-corrected values
    would chain mappings (V->W, then W->X) instead of collapsing them (V->X).

    Returns the corrections recorded, for a UI that wants to say what was learned."""
    if not (supplier_id and product_type) or not isinstance(final, dict):
        return []
    if not extracted:
        # CONFIRMED REAL BUG (audit): when extraction throws, the flow sets data = {} and a
        # human types the whole service in by hand. Comparing against {} turned every hand-
        # typed value into an "empty -> X" rule, and the next document that merely left a
        # field blank was silently pre-filled from an unrelated service. Nothing extracted
        # means nothing was corrected.
        return []
    row = _load(supplier_id, product_type)
    fields = row.setdefault("fields", {})
    now = datetime.now(timezone.utc).isoformat()
    recorded = []

    for field, new_value in final.items():
        if field in _NEVER_LEARN or field.startswith("_"):
            continue
        old_value = extracted.get(field)
        from_key = _norm(old_value)
        to_key = _norm(new_value)
        # None means "not a learnable shape" (a table, an over-long blob). Equal means the
        # human left it alone, which is not a correction.
        if from_key is None or to_key is None or from_key == to_key:
            continue

        bucket = fields.setdefault(field, {})
        entry = bucket.get(from_key)
        if entry and _norm(entry.get("to")) == to_key:
            # The same correction again - this is the confirmation that turns it into a rule.
            entry["count"] = int(entry.get("count", 0)) + 1
            entry["last_seen"] = now
        else:
            # Either brand new, or the human has changed their mind about what this value
            # should become. Replacing rather than accumulating means the newest decision
            # wins, and the count restarts so a reversal has to earn its confidence again.
            entry = {"from": old_value, "to": new_value, "count": 1,
                     "first_seen": now, "last_seen": now, "examples": []}
            bucket[from_key] = entry
        if service_label and service_label not in entry.get("examples", []):
            entry.setdefault("examples", []).append(service_label)
            entry["examples"] = entry["examples"][-3:]

        fields[field] = _prune(bucket)
        recorded.append({"field": field, "from": old_value, "to": new_value,
                         "count": entry["count"], "applies": entry["count"] >= _APPLY_AFTER
                         and not _apply_blocked(field)})

    if recorded and not _save(supplier_id, product_type, row):
        # Say nothing was learned rather than let the UI report a success the store refused.
        print("[extraction_memory] corrections could NOT be saved - nothing was learned")
        return []
    return recorded


def learned_for(supplier_id: str, product_type: str, field: str, value: Any) -> Optional[Dict[str, Any]]:
    """The mapping that would fire for this exact field/value, or None. Exposed separately
    so a caller can ask without mutating anything."""
    if _apply_blocked(field) or field in _NEVER_LEARN:
        return None
    from_key = _norm(value)
    if from_key is None:
        return None
    entry = _load(supplier_id, product_type).get("fields", {}).get(field, {}).get(from_key)
    if not entry or int(entry.get("count", 0)) < _APPLY_AFTER:
        return None
    if _norm(entry.get("to")) == from_key:
        return None
    return entry


def apply_learned(supplier_id: str, product_type: str, data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Pre-fill fields where this supplier's past corrections say the extractor is
    predictably wrong. Mutates `data` in place.

    Call this immediately after extraction and immediately after snapshot(), so the raw
    extractor output is preserved for record_corrections() to compare against.

    Returns one entry per field changed, so the review screen can show a person exactly
    what was altered and why. Nothing here is silent: an unexplained pre-filled value would
    be worse than no learning at all."""
    if not (supplier_id and product_type) or not isinstance(data, dict):
        return []
    row = _load(supplier_id, product_type)
    fields = row.get("fields", {})
    if not fields:
        return []

    applied = []
    for field, value in list(data.items()):
        if field in _NEVER_LEARN or field.startswith("_") or _apply_blocked(field):
            continue
        from_key = _norm(value)
        if from_key is None:
            continue
        entry = fields.get(field, {}).get(from_key)
        if not entry or int(entry.get("count", 0)) < _APPLY_AFTER:
            continue
        if _norm(entry.get("to")) == from_key:
            continue
        data[field] = entry["to"]
        applied.append({"field": field, "from": value, "to": entry["to"],
                        "count": int(entry.get("count", 0)),
                        "last_seen": entry.get("last_seen", "")})
    return applied


def snapshot(data: Dict[str, Any]) -> Dict[str, Any]:
    """A copy of the extractor's raw output, to compare against at publish time.

    Only scalars are kept. Nested tables are excluded on purpose: they are never learned,
    and copying them would mean holding a second full copy of every price grid in session
    state for the whole review."""
    return {k: v for k, v in (data or {}).items()
            if k not in _NEVER_LEARN and not k.startswith("_")
            and isinstance(v, (str, int, float, bool, type(None)))}


def list_learned(supplier_id: Optional[str] = None,
                 product_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """Everything learned, flattened for display. Sorted most-confirmed first so the rules
    actually shaping uploads are at the top."""
    rows = []
    for key, row in platform_store.get_namespace(_NAMESPACE).items():
        sid, _, ptype = key.partition("|")
        if supplier_id and str(supplier_id) != sid:
            continue
        if product_type and product_type != ptype:
            continue
        for field, bucket in (row or {}).get("fields", {}).items():
            for from_key, entry in (bucket or {}).items():
                count = int(entry.get("count", 0))
                rows.append({
                    "supplier_id": sid, "product_type": ptype, "field": field,
                    "from": entry.get("from"), "to": entry.get("to"),
                    "count": count, "last_seen": entry.get("last_seen", ""),
                    "examples": entry.get("examples", []),
                    "active": count >= _APPLY_AFTER and not _apply_blocked(field),
                    "blocked": _apply_blocked(field),
                    "from_key": from_key,
                })
    return sorted(rows, key=lambda r: (-r["count"], r["supplier_id"], r["product_type"],
                                       r["field"], str(r["from"])))


def forget(supplier_id: str, product_type: str, field: str, from_key: str) -> bool:
    """Delete one mapping. A human must always be able to remove something the platform
    learned - without this, a wrong lesson is permanent and the only escape is wiping the
    supplier's whole history."""
    row = _load(supplier_id, product_type)
    bucket = row.get("fields", {}).get(field, {})
    if from_key not in bucket:
        return False
    del bucket[from_key]
    if not bucket:
        row["fields"].pop(field, None)
    return _save(supplier_id, product_type, row)


def prune_old_mappings(max_age_days: int = 90) -> int:
    """Removes mappings that haven't been seen (confirmed OR freshly recorded) in
    max_age_days, across every supplier and product type. Returns the number removed.

    INVESTIGATION BEFORE BUILDING THIS (2026-08-12): the issue that requested this also
    proposed deleting anything with count < the confirmation threshold, on the theory that
    those are "never applied anyway". That is true at any single moment, but the whole
    point of an unconfirmed mapping (see record_corrections' docstring and the "observed"
    state in render_memory_panel) is that it SITS THERE waiting for a second document to
    confirm it into a real rule - one correction is as likely to be a typo as a pattern,
    two is a pattern. Deleting on count alone means a correction made once today can be
    wiped before a second contract ever arrives to confirm it, which breaks that mechanism
    outright for any supplier whose contracts don't arrive daily. Age is the right signal
    for "stale" instead: a mapping - confirmed or not - that nothing has hit in
    max_age_days is either a supplier who visibly changed their format, or a one-off that
    never recurred, and is safe to forget; if it's still true, two more documents relearn
    it.

    Deliberately manual-only (exposed as a button in render_memory_panel), not run at
    startup or on every publish - see forget()'s docstring: deleting something the
    platform learned must be a decision a person can see and reverse-by-re-teaching, not a
    background job whose only visible symptom is a rule that used to fire and quietly
    doesn't any more."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, int(max_age_days)))).isoformat()
    removed = 0
    for key, row in platform_store.get_namespace(_NAMESPACE).items():
        if not isinstance(row, dict) or "fields" not in row:
            continue
        sid, _, ptype = key.partition("|")
        fields = row.get("fields", {})
        changed = False
        for field in list(fields.keys()):
            bucket = fields[field]
            for from_key in list(bucket.keys()):
                entry = bucket[from_key] or {}
                # ISO-8601 UTC strings from datetime.now(timezone.utc).isoformat() sort
                # chronologically as plain strings - same trick _prune() already relies on.
                last_seen = entry.get("last_seen") or entry.get("first_seen") or ""
                if last_seen < cutoff:
                    del bucket[from_key]
                    removed += 1
                    changed = True
            if not bucket:
                fields.pop(field, None)
        if changed:
            _save(sid, ptype, row)
    platform_store.set(_PRUNE_META_NAMESPACE, _PRUNE_META_KEY, {
        "last_pruned_at": datetime.now(timezone.utc).isoformat(),
        "removed": removed,
        "max_age_days": int(max_age_days),
    })
    return removed


def last_prune_info() -> Optional[Dict[str, Any]]:
    """When prune_old_mappings() last ran and what it did, or None if it never has."""
    info = platform_store.get(_PRUNE_META_NAMESPACE, _PRUNE_META_KEY)
    return info if isinstance(info, dict) else None


# ----------------------------------------------------------------------
# Instructions typed into "Tell AI what to fix"
#
# CONFIRMED REAL REQUEST (product owner): "it would be extremely helpful if the included
# database could learn from the 'Tell AI what to fix' as this is the biggest issue."
#
# WHY THESE ARE THE BEST SIGNAL IN THE APP: a value correction says WHAT was wrong. An
# instruction says WHY, in the operator's own words - "the triple price is the third column,
# not the second", "this supplier writes the return leg first". That is a rule about how this
# supplier's documents read, and it is exactly the thing the extractor cannot work out on its
# own. Until now it was typed, used once, and discarded.
#
# Stored per supplier AND product type, because it is a fact about how one supplier writes one
# kind of document. Only instructions that actually CHANGED something are kept: a question
# ("what does the third column mean?") teaches nothing, and filling the store with questions
# would bury the rules.
_INSTRUCTION_NAMESPACE = "clarify_instructions"

# How many past instructions are fed into the next extraction. Enough to carry a supplier's
# real quirks, few enough that they cannot crowd out the document itself.
_MAX_INSTRUCTIONS_FED = 8
_MAX_INSTRUCTIONS_STORED = 40
_MAX_INSTRUCTION_CHARS = 400


def _instruction_key(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()[:_MAX_INSTRUCTION_CHARS]


def record_instruction(supplier_id: str, product_type: str, instruction: str,
                       changed_fields: Any = None) -> bool:
    """Remember an instruction that actually changed something.

    changed_fields is stored alongside so the memory panel can show what the instruction did -
    "you said this 3 times, and it changed price_list each time" is far easier to judge than
    the sentence on its own."""
    text = (instruction or "").strip()
    if not (supplier_id and product_type and text):
        return False
    if not changed_fields:
        # A question that changed nothing is not a rule about the supplier.
        return False
    key = _instruction_key(text)
    if not key:
        return False
    row = platform_store.get(_INSTRUCTION_NAMESPACE, _key(str(supplier_id), product_type)) or {}
    if not isinstance(row, dict):
        row = {}
    entries = row.get("instructions") or {}
    fields = sorted({str(f) for f in (changed_fields or [])})
    entry = entries.get(key) or {"text": text[:_MAX_INSTRUCTION_CHARS], "count": 0, "fields": []}
    entry["count"] = int(entry.get("count", 0)) + 1
    # Keep the FIRST wording. The key already treats "  the TRIPLE price..." and "The triple
    # price..." as the same instruction, so overwriting would let a hurried re-typing degrade
    # the considered original that gets fed to the model.
    entry.setdefault("text", text[:_MAX_INSTRUCTION_CHARS])
    entry["fields"] = sorted(set(entry.get("fields", [])) | set(fields))
    entry["last_seen"] = datetime.now(timezone.utc).isoformat()
    entries[key] = entry
    if len(entries) > _MAX_INSTRUCTIONS_STORED:
        ranked = sorted(entries.items(),
                        key=lambda kv: (kv[1].get("count", 0), kv[1].get("last_seen", "")),
                        reverse=True)
        entries = dict(ranked[:_MAX_INSTRUCTIONS_STORED])
    row["instructions"] = entries
    row["updated_at"] = datetime.now(timezone.utc).isoformat()
    return platform_store.set(_INSTRUCTION_NAMESPACE, _key(str(supplier_id), product_type), row)


def list_instructions(supplier_id: str, product_type: str) -> List[Dict[str, Any]]:
    """Instructions remembered for this supplier and product type, most-repeated first."""
    row = platform_store.get(_INSTRUCTION_NAMESPACE, _key(str(supplier_id), product_type)) or {}
    entries = (row or {}).get("instructions") or {}
    out = [dict(v, key=k) for k, v in entries.items() if isinstance(v, dict) and v.get("text")]
    return sorted(out, key=lambda e: (-int(e.get("count", 0)), e.get("last_seen", "")), reverse=False)


def forget_instruction(supplier_id: str, product_type: str, key: str) -> bool:
    row = platform_store.get(_INSTRUCTION_NAMESPACE, _key(str(supplier_id), product_type)) or {}
    entries = (row or {}).get("instructions") or {}
    if key not in entries:
        return False
    del entries[key]
    row["instructions"] = entries
    return platform_store.set(_INSTRUCTION_NAMESPACE, _key(str(supplier_id), product_type), row)


# HOUSE RULES: a rule that is true of the TRADE, not of one supplier.
#
# CONFIRMED REAL COMPLAINT (product owner): "The AI learning must understand basics, I repeat
# myself too often. As I have often the same problem." The cause was the SHAPE of the memory
# rather than its contents: everything learned was filed under one supplier and one product type,
# so "Nile Cruise prices are quoted per night" had to be taught again for every supplier who
# sells a Nile cruise. A rule about how the trade quotes prices is not a fact about one company,
# and filing it as one guarantees repetition.
#
# House rules are fed into EVERY extraction of that product type, for every supplier, on top of
# whatever that supplier has taught individually.
HOUSE_SCOPE = "__house__"
_MAX_HOUSE_RULES_FED = 12


def list_house_rules(product_type: str) -> List[Dict[str, Any]]:
    """Rules that apply to every supplier for this product type."""
    return list_instructions(HOUSE_SCOPE, product_type)


def add_house_rule(product_type: str, text: str) -> bool:
    """Promote a rule to house level. Recorded with a synthetic changed-field so it passes the
    same "only real rules are kept" gate that supplier instructions go through."""
    return record_instruction(HOUSE_SCOPE, product_type, text, ["__house_rule__"])


def forget_house_rule(product_type: str, key: str) -> bool:
    return forget_instruction(HOUSE_SCOPE, product_type, key)


def instruction_guidance(supplier_id: str, product_type: str) -> str:
    """Past instructions, as a block to put in front of the next extraction.

    Two layers, in this order: HOUSE RULES (true of every supplier for this product type), then
    THIS SUPPLIER'S own quirks. Both are framed as guidance rather than fact, because the
    document always wins - a corrective that was right for last season's rate sheet must not
    overwrite what this one plainly says."""
    blocks = []

    house = list_house_rules(product_type)[:_MAX_HOUSE_RULES_FED]
    if house and str(supplier_id) != HOUSE_SCOPE:
        blocks.append(
            "HOUSE RULES - these hold for EVERY supplier of this product type, and the operator "
            "has had to state them more than once. Apply them unless this document plainly "
            "contradicts them:\n"
            + "\n".join(f"- {e['text']}" for e in house))

    entries = list_instructions(supplier_id, product_type)[:_MAX_INSTRUCTIONS_FED]
    if entries:
        lines = []
        for e in entries:
            times = int(e.get("count", 0))
            suffix = f" (said {times}x)" if times > 1 else ""
            lines.append(f"- {e['text']}{suffix}")
        blocks.append(
            "THINGS A HUMAN HAS PREVIOUSLY HAD TO CORRECT ON THIS SUPPLIER'S DOCUMENTS. These are "
            "notes from the operator about how THIS supplier writes things, collected from earlier "
            "corrections. Apply them where they still fit this document, and ignore any that "
            "plainly do not - the document in front of you always wins:\n" + "\n".join(lines))

    return "\n\n".join(blocks)


def list_all_instructions() -> List[Dict[str, Any]]:
    """Every learned instruction across all suppliers, for the platform-wide memory panel.

    Sorted most-repeated first: an instruction typed five times is a rule about the supplier,
    while one typed once may just have been a one-off fix."""
    rows = []
    for key, row in platform_store.get_namespace(_INSTRUCTION_NAMESPACE).items():
        supplier_id, _, product_type = key.partition("|")
        for ikey, entry in ((row or {}).get("instructions") or {}).items():
            if not isinstance(entry, dict) or not entry.get("text"):
                continue
            rows.append({"supplier_id": supplier_id, "product_type": product_type,
                         "key": ikey, "text": entry["text"],
                         "count": int(entry.get("count", 0)),
                         "fields": entry.get("fields", []),
                         "last_seen": entry.get("last_seen", "")})
    return sorted(rows, key=lambda r: (-r["count"], r["supplier_id"], r["product_type"]))


def render_instruction_panel(supplier_id: str, product_type: str) -> None:
    """Show what has been learned from corrections for this supplier, with a way to delete."""
    import streamlit as st

    entries = list_instructions(supplier_id, product_type)
    if not entries:
        return
    with st.expander(f"🧠 {len(entries)} thing(s) learned from your past corrections for this "
                     f"supplier", expanded=False):
        st.caption("Typed into “Tell AI what to fix” on an earlier document, and now given to the "
                  "AI before it reads a new one. The document always wins over these.")
        for e in entries:
            cols = st.columns([6, 1])
            with cols[0]:
                times = int(e.get("count", 0))
                st.markdown(f"- {e['text']}" + (f"  ·  *said {times}×*" if times > 1 else ""))
                if e.get("fields"):
                    st.caption("changed: " + ", ".join(f"`{f}`" for f in e["fields"]))
            with cols[1]:
                if st.button("🗑️", key=f"em_forget_instr_{supplier_id}_{product_type}_{e['key']}",
                             help="Forget this"):
                    forget_instruction(supplier_id, product_type, e["key"])
                    st.rerun()


def prepare(supplier_id: str, product_type: str, item: Dict[str, Any],
            data_key: str = "data") -> List[Dict[str, Any]]:
    """The one call a flow makes right after extraction: snapshot the raw output, then
    apply whatever has been learned. Stores both on `item` so the review screen can show
    what changed and publish() can compare against the original.

    Kept here rather than repeated in each flow so all five product types capture
    corrections identically - a flow that snapshotted at the wrong moment would silently
    learn nothing, or learn nonsense, and nothing on screen would reveal it."""
    data = item.get(data_key) or {}
    item["_raw"] = snapshot(data)
    item["_committed"] = False
    applied = apply_learned(supplier_id, product_type, data)
    item["_learned_applied"] = applied
    return applied


def commit(supplier_id: str, product_type: str, item: Dict[str, Any],
           service_label: str = "", data_key: str = "data") -> List[Dict[str, Any]]:
    """The counterpart to prepare(), called on a SUCCESSFUL publish.

    Runs at most ONCE per extraction. CONFIRMED REAL BUG (audit): the Publish button stays
    live after a successful publish, and the app itself tells an operator to re-publish
    after a partial failure ("re-running is safe"). Each click called commit() again with
    the identical before/after, so one document could push a correction's confidence from 1
    to 2 and promote a single one-off edit into a supplier-wide rule that pre-fills every
    future upload. Confidence has to mean "seen on N separate documents", not "clicked N
    times". prepare() clears the flag, so a genuinely re-extracted item can commit again."""
    if item.get("_committed"):
        return []
    item["_committed"] = True
    return record_corrections(supplier_id, product_type, item.get("_raw") or {},
                              item.get(data_key) or {}, service_label)


def render_applied_banner(applied: List[Dict[str, Any]]) -> None:
    """Say, on the review screen, exactly which fields were pre-filled from past
    corrections. A pre-filled value that nobody announced is indistinguishable from the
    extractor having read the document correctly - which is precisely how a wrong lesson
    would slip through unnoticed."""
    if not applied:
        return
    import streamlit as st
    with st.expander(f"🧠 {len(applied)} field(s) pre-filled from your past corrections",
                     expanded=True):
        st.caption("The extractor produced these values, and you have corrected them the same "
                   "way before for this supplier. They are filled in for you — check them as "
                   "usual, and just edit any that are wrong; that teaches it the new answer.")
        for a in applied:
            st.markdown(f"- **{a['field']}**: `{a['from']}` → `{a['to']}`  "
                        f"<span style='color:#888'>(you changed this {a['count']}× before)</span>",
                        unsafe_allow_html=True)


def render_memory_panel(supplier_id: Optional[str] = None) -> None:
    """The audit screen. Everything the platform has learned, what is actually being
    applied, and a way to delete any of it."""
    import streamlit as st

    rows = list_learned(supplier_id)
    if not rows:
        st.caption("Nothing learned yet. Corrections you make on the review screen before "
                   "publishing are remembered here, and start being applied once the same "
                   f"correction has been made {_APPLY_AFTER} times.")
        return

    active = [r for r in rows if r["active"]]
    st.caption(f"{len(active)} rule(s) being applied, {len(rows) - len(active)} correction(s) "
               f"recorded but not applied.")

    with st.expander("🧹 Prune old corrections", expanded=False):
        st.caption("Removes mappings — applied or not — that haven't been recorded or "
                   "reconfirmed in a while. Useful once a supplier has visibly changed their "
                   "document style and an old rule would otherwise keep firing on new "
                   "contracts. Manual on purpose: deleting something the platform learned is "
                   "a one-way action, so it only happens when you ask for it here, never in "
                   "the background.")
        info = last_prune_info()
        if info:
            st.caption(f"Last run: {str(info.get('last_pruned_at', '?'))[:10]} — removed "
                       f"{info.get('removed', 0)} mapping(s) older than "
                       f"{info.get('max_age_days', '?')} day(s).")
        else:
            st.caption("Never run.")
        max_age = st.number_input("Remove mappings not seen in this many days", min_value=1,
                                  value=90, step=1, key="em_prune_max_age")
        if st.button("🧹 Prune now", key="em_prune_button"):
            n = prune_old_mappings(int(max_age))
            st.success(f"Removed {n} mapping(s) not seen in {int(max_age)} day(s).")
            st.rerun()

    for r in rows:
        if r["active"]:
            state = f"✅ applied (seen {r['count']}×)"
        elif r["blocked"]:
            # Explain the refusal - otherwise it reads like a bug that a 5×-confirmed
            # correction still isn't being used.
            state = f"👀 observed {r['count']}× — dates and prices are never auto-filled"
        else:
            state = f"👀 seen {r['count']}× — applied at {_APPLY_AFTER}"
        cols = st.columns([6, 1])
        with cols[0]:
            st.markdown(f"**{r['product_type']} · {r['field']}** (supplier {r['supplier_id']})  \n"
                        f"`{r['from']}` → `{r['to']}`  \n{state}")
        with cols[1]:
            if st.button("🗑️", key=f"em_forget_{r['supplier_id']}_{r['product_type']}_"
                                   f"{r['field']}_{r['from_key']}", help="Forget this"):
                forget(r["supplier_id"], r["product_type"], r["field"], r["from_key"])
                st.rerun()


def summary(supplier_id: str, product_type: str) -> Tuple[int, int]:
    """(mappings being applied, mappings still only observed) for this supplier."""
    active = observed = 0
    for r in list_learned(supplier_id, product_type):
        if r["active"]:
            active += 1
        else:
            observed += 1
    return active, observed
