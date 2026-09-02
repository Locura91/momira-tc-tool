"""
state_store.py — tracks what's already been translated, so re-running the
tool is safe (idempotent) and cheap.

THIS IS THE MOST EXPENSIVE THING THE PLATFORM REMEMBERS. Every entry here
represents translation work already paid for. If the record is lost, the next
run sees everything as new and re-translates content that never changed - and
the translation API bills for it again.

MOVED OFF THE LOCAL FILESYSTEM: this used to be a SQLite file (nbext_state.db)
sitting next to the app. On Streamlit Cloud that filesystem is wiped on every
redeploy and container restart, so in production the tracker was being reset
regularly and the re-translation cost was real, recurring, and completely
invisible - the app looked like it was working, it just redid paid work. State
now lives in platform_store: Postgres when DATABASE_URL is set, which survives
restarts.

The public API (compute_hash, and StateStore.get_state / upsert_state /
languages_needed) is unchanged, so every sync_*.py module, the translation tool
and the run_sync_*.py CLI scripts all call it exactly as before.
"""

import json
import hashlib
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

import platform_store

MODULE_BUILD = "2026-09-02-hotel-images-required"

_NAMESPACE = "translation_state"

# Only read, once, to migrate an existing local database. Nothing writes here now.
LEGACY_DB_PATH = os.getenv("LEGACY_TRANSLATION_DB_PATH", "nbext_state.db")

# Kept so run_sync_*.py, which reference state_store.DB_PATH, keep importing cleanly.
DB_PATH = LEGACY_DB_PATH


def compute_hash(fields: Dict[str, str]) -> str:
    """Deterministic hash of the English source fields, used to detect edits."""
    canonical = json.dumps(fields, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _key(entity_type: str, supplier_id: str, entity_id: str, option_code: str = "") -> str:
    """The old table's four-part primary key, flattened into one string. '|' is safe as a
    separator here because none of the four parts (entity type, numeric supplier id, TC
    id/code, option code) can contain it."""
    return f"{entity_type}|{supplier_id}|{entity_id}|{option_code}"


class StateStore:
    def __init__(self, db_path: str = None):
        # db_path is accepted and ignored so the existing run_sync_*.py CLI scripts keep
        # working unchanged. Where state actually lives is now decided by DATABASE_URL.
        self.db_path = db_path or LEGACY_DB_PATH
        self._migrate_legacy_if_present()

    def _migrate_legacy_if_present(self) -> None:
        """Copies an existing nbext_state.db into durable storage once, so upgrading
        doesn't discard translations already paid for. Only runs while the durable store
        still has no translation rows.

        CONFIRMED BUG FIX (full-app audit MED plausible, 2026-09-02): platform_store.get_namespace
        swallows every read failure and returns {} - identical to "this namespace genuinely has
        no rows yet" (the same ambiguity already fixed elsewhere in this codebase for the
        outreach duplicate-send guard). A momentary Postgres blip at exactly the wrong moment
        used to look exactly like "never migrated," re-triggering this migration path and
        overwriting whatever's currently in durable storage with the (possibly much older)
        legacy SQLite snapshot - the opposite of what this function exists to protect. Now checks
        platform_store.health() (a real round-trip, not a cache-backed read) before trusting an
        empty namespace read as genuinely empty; an unreachable store skips migration entirely
        rather than risking an overwrite, and simply gets tried again on the next run."""
        try:
            existing = platform_store.get_namespace(_NAMESPACE)
            if existing:
                return
            if not platform_store.health().get("ok"):
                print("⚠️ Skipping legacy translation-tracker migration check - the durable "
                      "store didn't answer a health check just now, so an empty read here can't "
                      "be trusted as genuinely empty. Will re-check on the next run.")
                return
            if not os.path.exists(self.db_path):
                return
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute("SELECT * FROM translation_state").fetchall()
            except sqlite3.Error:
                return
            finally:
                conn.close()
            migrated = 0
            for row in rows:
                platform_store.set(
                    _NAMESPACE,
                    _key(row["entity_type"], row["supplier_id"], row["entity_id"], row["option_code"] or ""),
                    {
                        "source_hash": row["source_hash"],
                        # sorted() to match what upsert_state writes, so a migrated row and a
                        # freshly-written one are byte-identical. languages_needed() compares
                        # via a set so order never affected correctness, but an inconsistent
                        # ordering would make any future diffing of these rows misleading.
                        "translated_languages": sorted(json.loads(row["translated_languages"])),
                        "last_synced_at": row["last_synced_at"],
                    },
                )
                migrated += 1
            if migrated:
                print(f"📦 Migrated {migrated} translation record(s) from {self.db_path} into "
                      f"durable storage - that work won't be re-translated or re-billed.")
        except Exception as e:
            print(f"⚠️ Could not migrate the legacy translation tracker ({e}) - continuing with "
                  f"durable storage only. Worst case, some content gets translated once more.")

    def get_state(self, entity_type: str, supplier_id: str, entity_id: str,
                  option_code: str = "") -> Optional[Dict[str, Any]]:
        return platform_store.get(_NAMESPACE,
                                   _key(entity_type, str(supplier_id), str(entity_id), option_code))

    def upsert_state(
        self,
        entity_type: str,
        supplier_id: str,
        entity_id: str,
        source_hash: str,
        translated_languages: List[str],
        option_code: str = "",
    ) -> bool:
        """CONFIRMED BUG FIX (full-app audit HIGH, 2026-09-01): this used to call
        platform_store.set() and discard its return value entirely - the exact silent
        re-billing this module's own docstring calls out ("if the record is lost, the next
        run sees everything as new and re-translates content that never changed - and the
        translation API bills for it again"). A transient write failure (a Postgres blip, a
        connection drop mid-sync) looked EXACTLY like a successful write to every caller - the
        sync just moved on to the next entity, believing the translation was recorded, when it
        silently wasn't. Every other durable store in this app (service_notes, supplier_images,
        cancellation_links) surfaces set()'s failure to the operator; this one didn't surface it
        anywhere at all, to anyone - not even a log line, let alone a UI. Now returns the
        success flag (matching platform_store.set's own contract, so a future caller CAN check
        it) and, since these sync_*.py runs are unattended CLI/scheduled jobs with no UI to show
        an st.error to, prints a loud, specific failure using the entity info already in scope
        here - the one choke point every upsert_state call already goes through, so no call site
        anywhere needed to change to get this visibility."""
        ok = platform_store.set(
            _NAMESPACE,
            _key(entity_type, str(supplier_id), str(entity_id), option_code),
            {
                "source_hash": source_hash,
                "translated_languages": sorted(translated_languages),
                "last_synced_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        if not ok:
            print(f"🔴 [state_store] FAILED to record translation state for "
                  f"{entity_type} supplier={supplier_id} entity={entity_id} "
                  f"option={option_code or '(none)'} - this content will look untranslated on "
                  f"the NEXT sync run and will be re-translated and re-billed even though "
                  f"nothing about it changed. Check DATABASE_URL / the platform_store connection.")
        return ok

    def clear_state(self, entity_type: str, supplier_id: str, entity_id: str, option_code: str = "") -> bool:
        """CONFIRMED BUG FIX (full-app audit HIGH, 2026-09-01): deletes this entity's tracked
        translation state entirely, so the next regular sync treats it as never-synced. Added
        for cancellation_bulk_transport.py's bulk cancellation-policy update, which - by design
        (see that module's own docstring on why it deliberately touches ONLY the EN datasheet) -
        changes the EN source without touching any other language's already-live text. Called
        right after a successful bulk update so this Transport's now-stale non-EN cancellation
        text isn't left sitting behind a tracker row that still (wrongly) claims it's up to
        date."""
        return platform_store.delete(_NAMESPACE, _key(entity_type, str(supplier_id), str(entity_id), option_code))

    def languages_needed(self, entity_type, supplier_id, entity_id, source_hash, target_languages, option_code=""):
        """
        Returns the subset of target_languages that still need translating:
        - all of them, if this entity has never been synced, or its English
          source changed since the last sync (source_hash differs)
        - just the missing ones, if the source is unchanged but the target
          language list grew since the last run
        - [] if everything is already up to date
        """
        state = self.get_state(entity_type, supplier_id, entity_id, option_code)
        if state is None or state["source_hash"] != source_hash:
            return list(target_languages)
        already_done = set(state["translated_languages"])
        return [lang for lang in target_languages if lang not in already_done]
