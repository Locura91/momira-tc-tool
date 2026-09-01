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

# Stamped on every delivery. app.py compares this against its own build string and says
# so on screen when they differ - a partial push (one file committed, another not) used to
# surface only as a traceback whose line numbers pointed at unrelated code.
MODULE_BUILD = "2026-09-01-audit-high-outreach-subsystem"

import json
import os
import sqlite3
import threading
import time
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
    """True when a Postgres URL is CONFIGURED. This is a cheap settings check, not proof
    that the database answers - a typo in the password looks identical to a working setup
    here. Use health() when the answer actually matters."""
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


# One reused Postgres connection, guarded by a lock.
#
# WHY REUSE: every get/set here is its own operation, and opening a fresh connection per
# call means a TCP handshake, TLS negotiation and auth round-trip to a remote database
# each time - roughly 100-300ms over the internet. That's tolerable for a page render
# doing a handful of reads, but the translation sync calls languages_needed() once per
# entity in a loop; at a few hundred entities, per-call connections would add minutes of
# pure connection overhead to a run and look like the tool had hung.
#
# WHY A LOCK: psycopg2 connections are not safe for concurrent use, and Streamlit serves
# each browser session on its own thread. Serializing access is the simple, correct
# choice at this volume - these are millisecond queries, not long transactions.
_PG_CONN = None
_PG_CONN_URL: Optional[str] = None
_PG_LOCK = threading.Lock()


def _reset_pg_connection():
    global _PG_CONN, _PG_CONN_URL
    if _PG_CONN is not None:
        try:
            _PG_CONN.close()
        except Exception:
            pass
    _PG_CONN = None
    _PG_CONN_URL = None


def _live_pg_connection(driver, url):
    """Returns the cached connection, reconnecting if it's missing, closed, or pointed at
    a different URL. A connection dropped by the server (idle timeout, pooler recycling,
    a Supabase restart) shows up as closed != 0 and is transparently replaced."""
    global _PG_CONN, _PG_CONN_URL
    if _PG_CONN is not None and _PG_CONN_URL == url:
        try:
            if _PG_CONN.closed == 0:
                return _PG_CONN
        except Exception:
            pass
        _reset_pg_connection()
    _PG_CONN = driver.connect(url)
    _PG_CONN_URL = url
    return _PG_CONN


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
            with _PG_LOCK:
                conn = _live_pg_connection(driver, url)
                try:
                    yield conn, True
                    conn.commit()
                except Exception:
                    # Roll back so a failed statement can't leave the reused connection in
                    # an aborted transaction, where every later query fails with
                    # "current transaction is aborted" until it's reset. Then drop the
                    # connection entirely, so the next call reconnects cleanly rather than
                    # inheriting whatever broke it.
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    _reset_pg_connection()
                    raise
            return

    conn = sqlite3.connect(_LOCAL_DB_PATH)
    try:
        yield conn, False
        conn.commit()
    finally:
        conn.close()


def _ensure_schema(conn, is_postgres: bool) -> None:
    global _initialized_for
    # Keyed by the actual URL, not the constant string "pg". CONFIRMED REAL BUG (audit):
    # with a fixed marker, pointing DATABASE_URL at a different database in a live process -
    # correcting a typo'd password in Streamlit secrets does exactly this - left the flag
    # saying "schema already created", so every read and write on the new database failed
    # with "no such table" and was swallowed by the callers' except blocks. The app kept
    # running and silently forgot every learned rule and standing note.
    marker = (_database_url() or "pg") if is_postgres else _LOCAL_DB_PATH
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


# ----------------------------------------------------------------------
# Health check
#
# WHY THIS EXISTS: is_durable() only asks "is DATABASE_URL set?". Every way this can be
# misconfigured - wrong password, wrong host, project paused, IPv6-only direct connection
# string used from an IPv4 host - passes that check and then fails at the first read. And
# every read here deliberately swallows its exception and returns None, because losing a
# cache lookup must never abort an upload. Those two behaviours combine badly: the app
# says "durable", writes nothing, reads nothing, and looks entirely healthy while
# forgetting everything. health() is the only thing in this module that does a REAL
# round trip and reports the raw failure.
_HEALTH_CACHE = None  # (url, result, monotonic timestamp)
_HEALTH_TTL_SECONDS = 60
_HEALTH_NAMESPACE = "__healthcheck__"


def _scrub(text: str, url: Optional[str]) -> str:
    """Driver errors shouldn't quote the password back into a UI or a log. psycopg2 does
    not normally include it, but 'normally' is not a guarantee worth taking with a
    credential, so it is removed explicitly."""
    if not url:
        return text
    try:
        creds = url.split("://", 1)[1].split("@", 1)[0]
        password = creds.split(":", 1)[1] if ":" in creds else ""
    except Exception:
        password = ""
    if password and password in text:
        text = text.replace(password, "***")
    return text


def health(force: bool = False) -> Dict[str, Any]:
    """Actually writes a row, reads it back and deletes it. Returns:

        mode    "postgres" | "local"
        ok      did the round trip succeed
        durable will this data still be here after a redeploy
        detail  one line for a human
        error   the raw (password-scrubbed) failure, or None

    Cached for a minute, because Streamlit re-runs the whole script on every widget
    interaction and this must not become a database round trip per keystroke. Pass
    force=True from a "test connection" button."""
    global _HEALTH_CACHE
    url = _database_url()
    now = time.monotonic()
    if not force and _HEALTH_CACHE is not None:
        cached_url, cached_result, ts = _HEALTH_CACHE
        if cached_url == url and (now - ts) < _HEALTH_TTL_SECONDS:
            return dict(cached_result)

    if url is None:
        result = {"mode": "local", "ok": True, "durable": False,
                  "detail": describe(), "error": None}
    elif _psycopg() is None:
        result = {"mode": "local", "ok": False, "durable": False,
                  "detail": "DATABASE_URL is set, but the psycopg2 driver is not installed, "
                            "so a local file is being used and nothing will survive a redeploy.",
                  "error": "psycopg2 is not installed - add psycopg2-binary to requirements.txt"}
    else:
        try:
            probe = {"checked_at": datetime.now(timezone.utc).isoformat()}
            payload = json.dumps(probe)
            with _connect() as (conn, is_pg):
                _ensure_schema(conn, is_pg)
                p = _ph(is_pg)
                cur = conn.cursor()
                cur.execute(
                    f"""INSERT INTO platform_state (namespace, key, value, updated_at)
                        VALUES ({p}, {p}, {p}, {p})
                        ON CONFLICT (namespace, key)
                        DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
                    (_HEALTH_NAMESPACE, "probe", payload, probe["checked_at"]),
                )
                # Read it back rather than trusting the write. A connection can be open and
                # a permission or replica problem still make the write a no-op.
                cur.execute(
                    f"SELECT value FROM platform_state WHERE namespace={p} AND key={p}",
                    (_HEALTH_NAMESPACE, "probe"))
                row = cur.fetchone()
            if not row or json.loads(row[0]) != probe:
                raise RuntimeError("the probe row was written but could not be read back")
            result = {"mode": "postgres", "ok": True, "durable": True,
                      "detail": describe(), "error": None}
        except Exception as e:
            result = {"mode": "postgres", "ok": False, "durable": False,
                      "detail": "DATABASE_URL is set, but the database did not answer. Nothing "
                                "is being remembered.",
                      "error": _scrub(f"{type(e).__name__}: {e}", url).strip()}

    _HEALTH_CACHE = (url, result, now)
    return dict(result)


def stats() -> Dict[str, int]:
    """Row count per namespace - for a UI panel showing what the platform remembers. The
    health probe's own row is hidden; it is bookkeeping, not something the platform learned."""
    try:
        with _connect() as (conn, is_pg):
            _ensure_schema(conn, is_pg)
            cur = conn.cursor()
            cur.execute("SELECT namespace, COUNT(*) FROM platform_state GROUP BY namespace")
            return {ns: int(n) for ns, n in cur.fetchall() if ns != _HEALTH_NAMESPACE}
    except Exception as e:
        print(f"[platform_store] stats failed: {e}")
        return {}
