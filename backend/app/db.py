"""SQLite access: one short-lived connection per operation, WAL mode."""
import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterable

from . import config

MIGRATIONS: list[str] = [
    # 1: initial schema
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        password_hash TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE TABLE sessions (
        token TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        last_seen REAL NOT NULL
    );
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE proxies (
        id INTEGER PRIMARY KEY,
        label TEXT NOT NULL,
        url_enc TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        success_count INTEGER NOT NULL DEFAULT 0,
        fail_count INTEGER NOT NULL DEFAULT 0,
        consecutive_fails INTEGER NOT NULL DEFAULT 0,
        cooldown_until REAL,
        last_ok_at REAL,
        last_checked_at REAL,
        last_error TEXT,
        last_latency_ms INTEGER,
        created_at REAL NOT NULL
    );
    CREATE TABLE accounts (
        id INTEGER PRIMARY KEY,
        label TEXT NOT NULL,
        cookies_enc TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'unknown',
        last_checked_at REAL,
        last_error TEXT,
        proxy_id INTEGER REFERENCES proxies(id) ON DELETE SET NULL,
        cookies_updated_at REAL NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE TABLE playlists (
        id INTEGER PRIMARY KEY,
        url TEXT NOT NULL,
        yt_id TEXT,
        title TEXT,
        folder TEXT,
        account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
        proxy_mode TEXT NOT NULL DEFAULT 'default',
        proxy_id INTEGER REFERENCES proxies(id) ON DELETE SET NULL,
        schedule TEXT NOT NULL DEFAULT '',
        enabled INTEGER NOT NULL DEFAULT 1,
        options TEXT NOT NULL DEFAULT '{}',
        on_removed TEXT NOT NULL DEFAULT 'keep',
        redownload_missing INTEGER NOT NULL DEFAULT 1,
        entry_count INTEGER,
        last_sync_at REAL,
        last_sync_status TEXT,
        last_error TEXT,
        next_run_at REAL,
        created_at REAL NOT NULL
    );
    CREATE TABLE videos (
        id TEXT PRIMARY KEY,
        title TEXT,
        channel TEXT,
        channel_id TEXT,
        duration REAL,
        thumbnail TEXT,
        upload_date TEXT,
        description TEXT,
        yt_artist TEXT,
        yt_track TEXT,
        yt_album TEXT,
        yt_release_year INTEGER,
        updated_at REAL NOT NULL
    );
    CREATE TABLE items (
        id INTEGER PRIMARY KEY,
        playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
        video_id TEXT NOT NULL REFERENCES videos(id),
        position INTEGER,
        status TEXT NOT NULL DEFAULT 'pending',
        file_path TEXT,
        file_size INTEGER,
        error TEXT,
        error_kind TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        first_seen_at REAL NOT NULL,
        removed_at REAL,
        downloaded_at REAL,
        meta TEXT,
        meta_override TEXT,
        updated_at REAL NOT NULL,
        UNIQUE (playlist_id, video_id)
    );
    CREATE INDEX items_playlist_status ON items(playlist_id, status);
    CREATE TABLE jobs (
        id INTEGER PRIMARY KEY,
        type TEXT NOT NULL,
        playlist_id INTEGER REFERENCES playlists(id) ON DELETE SET NULL,
        title TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued',
        params TEXT NOT NULL DEFAULT '{}',
        stats TEXT NOT NULL DEFAULT '{}',
        error TEXT,
        trigger TEXT NOT NULL DEFAULT 'manual',
        created_at REAL NOT NULL,
        started_at REAL,
        finished_at REAL
    );
    CREATE INDEX jobs_created ON jobs(created_at DESC);
    """,
    # 2: cross-provider transfers
    """
    CREATE TABLE connections (
        id INTEGER PRIMARY KEY,
        provider TEXT NOT NULL,
        label TEXT NOT NULL,
        credentials_enc TEXT NOT NULL DEFAULT '',
        account_id INTEGER REFERENCES accounts(id) ON DELETE CASCADE,
        status TEXT NOT NULL DEFAULT 'unknown',
        last_error TEXT,
        last_checked_at REAL,
        created_at REAL NOT NULL
    );
    CREATE TABLE transfers (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        a_conn_id INTEGER NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
        a_playlist_id TEXT NOT NULL,
        a_playlist_name TEXT,
        b_conn_id INTEGER NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
        b_playlist_id TEXT,
        b_playlist_name TEXT,
        keep_synced INTEGER NOT NULL DEFAULT 0,
        two_way INTEGER NOT NULL DEFAULT 0,
        sync_removals INTEGER NOT NULL DEFAULT 0,
        add_uncertain INTEGER NOT NULL DEFAULT 0,
        download INTEGER NOT NULL DEFAULT 0,
        download_playlist_id INTEGER REFERENCES playlists(id) ON DELETE SET NULL,
        schedule TEXT NOT NULL DEFAULT '',
        enabled INTEGER NOT NULL DEFAULT 1,
        next_run_at REAL,
        last_run_at REAL,
        last_status TEXT,
        last_error TEXT,
        stats TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL
    );
    CREATE TABLE transfer_items (
        id INTEGER PRIMARY KEY,
        transfer_id INTEGER NOT NULL REFERENCES transfers(id) ON DELETE CASCADE,
        side TEXT NOT NULL,
        track_id TEXT NOT NULL,
        meta TEXT NOT NULL DEFAULT '{}',
        position INTEGER,
        present INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'new',
        match_id TEXT,
        match_meta TEXT,
        score REAL,
        candidates TEXT,
        manual INTEGER NOT NULL DEFAULT 0,
        note TEXT,
        first_seen_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        UNIQUE (transfer_id, side, track_id)
    );
    CREATE INDEX transfer_items_status ON transfer_items(transfer_id, side, status);
    CREATE TABLE match_cache (
        src_provider TEXT NOT NULL,
        src_id TEXT NOT NULL,
        dst_provider TEXT NOT NULL,
        dst_id TEXT,
        score REAL,
        meta TEXT,
        checked_at REAL NOT NULL,
        PRIMARY KEY (src_provider, src_id, dst_provider)
    );
    ALTER TABLE jobs ADD COLUMN transfer_id INTEGER REFERENCES transfers(id) ON DELETE SET NULL;
    """,
    # 0.2.0: can the linked account edit the YouTube playlist (cached)
    """
    ALTER TABLE playlists ADD COLUMN writable INTEGER;
    ALTER TABLE playlists ADD COLUMN access_reason TEXT;
    ALTER TABLE playlists ADD COLUMN access_checked_at REAL;
    """,
]


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


@contextmanager
def tx():
    """Transaction context: yields a connection, commits on success."""
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def init() -> None:
    config.ensure_dirs()
    conn = _connect()
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        version = row[0] if row else 0
        if not row:
            conn.execute("INSERT INTO schema_version (version) VALUES (0)")
        for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
            conn.executescript("BEGIN;" + sql + f"; UPDATE schema_version SET version = {i}; COMMIT;")
    finally:
        conn.close()


def rows(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    conn = _connect()
    try:
        return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
    finally:
        conn.close()


def row(sql: str, params: Iterable[Any] = ()) -> dict | None:
    conn = _connect()
    try:
        r = conn.execute(sql, tuple(params)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def scalar(sql: str, params: Iterable[Any] = ()) -> Any:
    conn = _connect()
    try:
        r = conn.execute(sql, tuple(params)).fetchone()
        return r[0] if r else None
    finally:
        conn.close()


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    """Run a write statement; returns lastrowid."""
    conn = _connect()
    try:
        cur = conn.execute(sql, tuple(params))
        return cur.lastrowid
    finally:
        conn.close()


def update(table: str, id_: Any, fields: dict, key: str = "id") -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    execute(f"UPDATE {table} SET {cols} WHERE {key} = ?", [*fields.values(), id_])


def insert(table: str, fields: dict) -> int:
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    return execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(fields.values()))


def now() -> float:
    return time.time()


def jload(s: str | None, default: Any = None) -> Any:
    if not s:
        return default
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return default


def jdump(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))
