"""
platform_store.py — the platform's one durable key/value store.

WHY THIS EXISTS: everything the platform remembers between runs used to be
written to a file sitting next to the app - transfer_match_store.json,
transport_match_store.json, nbext_state.db. On Streamlit Cloud that filesystem
is EPHEMERAL: it is wiped on every redeploy and every container restart. The
consequences were real and were already happening in production:

  * the translation tracker forgot what it had translated, so the next run
    re-translated unchanged content and the translation API was PAID FOR THE
    SAME WORK AGAIN;
  * the Transfer/Transport route -> Travel Compositor id mappings forgot every
    match a human had confirmed, so those confirmations had to be repeated.

Neither failure announces itself. The app looks like it's working; it just
quietly does work it already did.

HOW IT WORKS: one table, namespaced key/value pairs holding JSON.

  * If DATABASE_URL is set, Postgres is used and the data genuinely survives
    redeploys. Any hosted Postgres works (Neon, Supabase, RDS...). This is the
    intended production setup.
  * If it isn't set, a local SQLite file is used instead. That keeps local
    development and tests working with zero configuration, and preserves the
    previous behaviour exactly - including its impermanence. is_durable()
    reports which one is live so the UI can say so out loud rather than
    letting an operator assume their data is safe.

The API is deliberately tiny (get/set/delete/get_namespace) because three very
different callers share it: the translation tracker's per-entity state, and the
two route matchers' supplier -> {route: id} dictionaries. Keeping it small is
what let all three move over without changing any of their own public APIs -
see state_store.py, transfer_matcher.py and transport_matcher.py.
"""
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Optional

_LOCAL_DB_PATH = os.getenv(
    "PLATFORM_STORE_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "platform_state.db"),
)

_INIT_LOCK = threading.Lock()
_initialized_for: Optional[str] = None


def _database_url() -> Optional[str]:
    """Read at call time, not import time, so Streamlit's secrets loading (which
    populates os.environ before the tools run) is always picked up."""
    url = (os.getenv("DATABASE_URL") or "").strip()
    return url or None


def is_durable() -> bool:
    """True when state survives a redeploy. False means a local file is in use, which
    on Streamlit Cloud is wiped on restart."""
    return _database_url() is not None


def describe() -> str:
    """One-line human description of where state lives, for the UI to display."""
    if is_durable():
        url = _database_url() or ""
        # Never surface credentials - show only the host.
        host = url.split("@")[-1].split("/")[0] if "@" in url else "database"
        return f"Postgres ({host}) — survives redeploys"
    return f"local file ({os.path.basename(_LOCAL_DB_PATH)}) — lost on redeploy"


def _psycopg():
    """Imported lazily so the platform runs fine without the Postgres driver installed -
    it's only needed when DATABASE_URL is actually configured."""
    try:
        import psycopg2
        return psycopg2
    except ImportError:
        return None


@contextmanager
def _connect():
    """Yields (connection, is_postgres). Postgres when DATABASE_URL is set and the
    driver is importable; SQLite otherwise.

    A missing psycopg2 with DATABASE_URL set falls back to SQLite rather than crashing -
    losing durability is bad, but taking the whole platform down over a missing optional
    dependency is worse. is_durable() would still claim True in that case, so the
    fallback is announced loudly on the console."""
    url = _database_url()
    if url:
        driver = _psycopg()
        if driver is None:
            print("[platform_store] DATABASE_URL is set but psycopg2 isn't installed - "
                  "falling back to a LOCAL file, which will NOT survive a redeploy. "
                  "Add psycopg2-binary to requirements.txt.")
        else:
            conn = driver.connect(url)
            try:
                yield conn, True
                conn.commit()
            finally:
                conn.close()
            return

    conn = sqlite3.connect(_LOCAL_DB_PATH)
    try:
        yield conn, False
        conn.commit()
    finally:
        conn.close()


def _ensure_schema(conn, is_postgres: bool) -> None:
    global _initialized_for
    marker = "pg" if is_postgres else _LOCAL_DB_PATH
    with _INIT_LOCK:
        if _initialized_for == marker:
            return
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_state (
                namespace  TEXT NOT NULL,
                key        TEXT NOT NULL,
                value      TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (namespace, key)
            )
        """)
        conn.commit()
        _initialized_for = marker


def _ph(is_postgres: bool) -> str:
    """Postgres uses %s placeholders, SQLite uses ?."""
    return "%s" if is_postgres else "?"


def get(namespace: str, key: str) -> Optional[Any]:
    """Returns the stored JSON value, or None if absent."""
    try:
        with _connect() as (conn, is_pg):
            _ensure_schema(conn, is_pg)
            p = _ph(is_pg)
            cur = conn.cursor()
            cur.execute(f"SELECT value FROM platform_state WHERE namespace={p} AND key={p}",
                        (namespace, key))
            row = cur.fetchone()
            return json.loads(row[0]) if row else None
    except Exception as e:
        print(f"[platform_store] read failed for {namespace}/{key}: {e}")
        return None


def get_namespace(namespace: str) -> Dict[str, Any]:
    """Returns every key/value in a namespace as a dict. Used by the matchers, which
    think in terms of one dictionary per supplier."""
    try:
        with _connect() as (conn, is_pg):
            _ensure_schema(conn, is_pg)
            p = _ph(is_pg)
            cur = conn.cursor()
            cur.execute(f"SELECT key, value FROM platform_state WHERE namespace={p}", (namespace,))
            return {k: json.loads(v) for k, v in cur.fetchall()}
    except Exception as e:
        print(f"[platform_store] namespace read failed for {namespace}: {e}")
        return {}


def set(namespace: str, key: str, value: Any) -> bool:
    """Upserts a JSON value. Returns False on failure rather than raising - losing a
    cache write should never abort the upload or translation that triggered it."""
    try:
        payload = json.dumps(value)
        now = datetime.now(timezone.utc).isoformat()
        with _connect() as (conn, is_pg):
            _ensure_schema(conn, is_pg)
            p = _ph(is_pg)
            cur = conn.cursor()
            cur.execute(
                f"""INSERT INTO platform_state (namespace, key, value, updated_at)
                    VALUES ({p}, {p}, {p}, {p})
                    ON CONFLICT (namespace, key)
                    DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
                (namespace, key, payload, now),
            )
        return True
    except Exception as e:
        print(f"[platform_store] write failed for {namespace}/{key}: {e}")
        return False


def delete(namespace: str, key: str) -> bool:
    try:
        with _connect() as (conn, is_pg):
            _ensure_schema(conn, is_pg)
            p = _ph(is_pg)
            conn.cursor().execute(
                f"DELETE FROM platform_state WHERE namespace={p} AND key={p}", (namespace, key))
        return True
    except Exception as e:
        print(f"[platform_store] delete failed for {namespace}/{key}: {e}")
        return False


def stats() -> Dict[str, int]:
    """Row count per namespace - for a UI panel showing what the platform remembers."""
    try:
        with _connect() as (conn, is_pg):
            _ensure_schema(conn, is_pg)
            cur = conn.cursor()
            cur.execute("SELECT namespace, COUNT(*) FROM platform_state GROUP BY namespace")
            return {ns: int(n) for ns, n in cur.fetchall()}
    except Exception as e:
        print(f"[platform_store] stats failed: {e}")
        return {}
