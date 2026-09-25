"""
draft_autosave.py - generic, flow-agnostic persistence for in-progress wizard state.

CONFIRMED REAL PRODUCT-OWNER REQUEST (2026-09-17): "before th publish button, humans must be
able to change the modalities, to change the main information without loosing all the
information and without starting from scratch." Clarified in follow-up: "any go back and not
only within closedtours. That is a general issue, because if the human reloads the page all
information is gone, if human wants to change the error so far the human has to start all over."

THE PROBLEM: st.session_state lives only in this Streamlit server process's memory for the
current browser session. A hard page reload, a dropped connection, or the Streamlit Cloud
container recycling all start a brand-new session with completely empty state - every extracted
field, every reviewed/edited price row, every Modality already added to the queue, the whole
in-progress wizard - silently gone, with no way back except re-uploading every document and
re-running every extraction from zero. This is true of EVERY flow (ClosedTour, Ticket, Transfer,
Transport, Hotel) equally - it's a property of st.session_state itself, not any one flow's logic.

THE FIX: every rerun, restore_and_autosave() (called once, near the very top of app.py, before
any flow-specific rendering, so it covers every flow automatically):
  1. Looks up a draft id stashed in the page's own URL query string. Unlike session_state, a
     query string survives a plain page reload (it's part of the URL itself) - so it's what
     lets this session's saved state be found again after one.
  2. On the FIRST rerun of a fresh session only, checks durable storage (platform_store - the
     same Postgres-backed store already used for translation state and route matches, so this
     genuinely survives Streamlit Cloud redeploys/container recycles too, not just a same-
     container reload) for a saved draft under that id. If one exists, shows a one-time
     Restore/Discard choice instead of silently guessing.
  3. Every rerun after that (including the very first one, when nothing was found), re-saves
     the CURRENT session_state back to durable storage - skipped entirely when nothing has
     actually changed since the last save, so idle reruns don't spam the database.

WHAT GETS SAVED: only JSON-serializable session_state values (see _json_safe_snapshot) -
uploaded-file objects, raw embedded-image bytes, and similar are silently skipped. This is a
deliberate boundary: the STRUCTURED data a human spends real time on (extracted fields, price
lists, stop sales, Modality queues, wizard step/phase flags, typed-in text...) is exactly what's
expensive to redo, and exactly what round-trips through JSON cleanly. A skipped image thumbnail
on the review screen is a minor cosmetic gap after a restore, not a redo-from-scratch one - a
human can always re-upload the same document to re-populate images if it matters.

A restored value has been through one JSON round-trip, so a tuple in the original session_state
comes back as a list, and a dict's int keys come back as strings (ordinary JSON behavior) - a
trade-off accepted for the enormous alternative (bespoke serialization for every one of the many
different shapes of data this app's dozen-plus wizards keep in session_state).
"""
import hashlib
import json
import time
import uuid
from typing import Any, Dict

import streamlit as st

import platform_store

MODULE_BUILD = "2026-09-25-transfer-image-bulk-verify-public-url-fix"

_NAMESPACE = "wizard_drafts"
_QUERY_PARAM = "draft"
_RESTORE_CHECKED_KEY = "_draft_restore_checked"
_PENDING_RESTORE_KEY = "_draft_pending_restore"
_PENDING_RESTORE_ID_KEY = "_draft_pending_restore_id"
_LAST_SAVED_HASH_KEY = "_draft_last_saved_hash"

# Bookkeeping keys this module itself owns - never persisted (even though some would round-trip
# through JSON fine) and never restored, so a restore can't accidentally clobber THIS rerun's own
# in-progress restore-check state.
_NEVER_PERSIST_PREFIX = "_draft_"


def _is_json_safe(value: Any) -> bool:
    try:
        json.dumps(value)
        return True
    except (TypeError, ValueError):
        return False


def _json_safe_snapshot() -> Dict[str, Any]:
    """Every current session_state key/value that round-trips through JSON cleanly - see this
    module's own docstring for why that's the right boundary (structured review data yes,
    uploaded-file objects/raw image bytes no)."""
    return {
        key: value for key, value in st.session_state.items()
        if not key.startswith(_NEVER_PERSIST_PREFIX) and _is_json_safe(value)
    }


def _snapshot_hash(snapshot: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def get_draft_id() -> str:
    """The id that ties THIS browser tab to its saved draft - stashed in the URL's own query
    string (not session_state) specifically because it must survive a plain page reload, which
    st.session_state itself does not. Stable for the lifetime of the tab/URL - a human sharing
    or bookmarking the URL would also share the draft id, which is fine (there's nothing secret
    in a wizard-in-progress) but not a goal either."""
    existing = st.query_params.get(_QUERY_PARAM)
    if existing:
        return existing
    new_id = uuid.uuid4().hex[:24]
    st.query_params[_QUERY_PARAM] = new_id
    return new_id


def discard_draft(draft_id: str) -> None:
    """Deletes this draft from durable storage. Called both when a human explicitly chooses
    'Discard it, start fresh' on the restore banner below, and by clear_on_publish_success once
    a flow's publish actually succeeds - either way, there's nothing left worth protecting
    against a reload, and leaving it behind would just get offered back as 'unfinished work'
    next time this same tab/URL is opened."""
    platform_store.delete(_NAMESPACE, draft_id)
    st.session_state[_LAST_SAVED_HASH_KEY] = None


def clear_on_publish_success() -> None:
    """Call this right after a flow's publish has actually succeeded. Clears the saved draft for
    THIS tab without touching the live session_state the rest of this rerun still needs to
    render its own success screen from - the next autosave (see restore_and_autosave) simply
    starts a fresh snapshot from whatever session_state looks like at that point."""
    draft_id = st.query_params.get(_QUERY_PARAM)
    if draft_id:
        platform_store.delete(_NAMESPACE, draft_id)


def _render_restore_banner(draft_id: str, saved: Dict[str, Any]) -> None:
    saved_at = saved.get("saved_at") or "an earlier session"
    st.warning(
        f"💾 Found unfinished work saved from **{saved_at}** - a page reload (or the app "
        f"restarting) doesn't have to mean starting over."
    )
    col1, col2 = st.columns(2)
    with col1:
        if st.button("↩️ Restore my progress", type="primary", key="_draft_restore_btn"):
            st.session_state.pop(_PENDING_RESTORE_KEY, None)
            st.session_state.pop(_PENDING_RESTORE_ID_KEY, None)
            for key, value in (saved.get("data") or {}).items():
                st.session_state[key] = value
            st.rerun()
    with col2:
        if st.button("🗑️ Discard it, start fresh", key="_draft_discard_btn"):
            discard_draft(draft_id)
            st.session_state.pop(_PENDING_RESTORE_KEY, None)
            st.session_state.pop(_PENDING_RESTORE_ID_KEY, None)
            st.rerun()


def _autosave(draft_id: str) -> None:
    snapshot = _json_safe_snapshot()
    if not snapshot:
        return
    snapshot_hash = _snapshot_hash(snapshot)
    if st.session_state.get(_LAST_SAVED_HASH_KEY) == snapshot_hash:
        return  # nothing changed since the last save - skip the write
    ok = platform_store.set(_NAMESPACE, draft_id, {
        "data": snapshot,
        "saved_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
    })
    if ok:
        st.session_state[_LAST_SAVED_HASH_KEY] = snapshot_hash


def restore_and_autosave() -> None:
    """Call this ONCE, near the very top of app.py, before any flow-specific rendering - see
    this module's own docstring for the full mechanism. Safe to call on every rerun."""
    draft_id = get_draft_id()

    if not st.session_state.get(_RESTORE_CHECKED_KEY):
        st.session_state[_RESTORE_CHECKED_KEY] = True
        saved = platform_store.get(_NAMESPACE, draft_id)
        if saved and saved.get("data"):
            st.session_state[_PENDING_RESTORE_KEY] = saved
            st.session_state[_PENDING_RESTORE_ID_KEY] = draft_id

    pending = st.session_state.get(_PENDING_RESTORE_KEY)
    if pending:
        _render_restore_banner(draft_id, pending)
        st.stop()

    _autosave(draft_id)
