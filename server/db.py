"""SQLite access (stdlib). One short-lived connection per transaction, WAL, migrations
tracked with PRAGMA user_version. Nothing touches the disk until the first use."""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

MIGRATIONS = [
    [
        """CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            pw_hash TEXT NOT NULL,
            verified INTEGER NOT NULL DEFAULT 0,
            simbrief_id TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL)""",
        """CREATE TABLE email_tokens (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            new_email TEXT NOT NULL DEFAULT '',
            expires_at REAL NOT NULL,
            used_at REAL)""",
        "CREATE INDEX email_tokens_user ON email_tokens(user_id)",
        """CREATE TABLE sessions (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL UNIQUE,
            csrf TEXT NOT NULL,
            created_at REAL NOT NULL,
            last_seen REAL NOT NULL)""",
        "CREATE INDEX sessions_user ON sessions(user_id)",
        """CREATE TABLE rate_events (
            id INTEGER PRIMARY KEY,
            bucket TEXT NOT NULL,
            key TEXT NOT NULL,
            ts REAL NOT NULL)""",
        "CREATE INDEX rate_events_lookup ON rate_events(bucket, key, ts)",
        """CREATE TABLE login_failures (
            id INTEGER PRIMARY KEY,
            key TEXT NOT NULL,
            ts REAL NOT NULL)""",
        "CREATE INDEX login_failures_lookup ON login_failures(key, ts)",
    ],
    [  # 2: indexes used by the periodic cleanup
        "CREATE INDEX rate_events_ts ON rate_events(ts)",
        "CREATE INDEX login_failures_ts ON login_failures(ts)",
        "CREATE INDEX sessions_seen ON sessions(last_seen)",
        "CREATE INDEX email_tokens_expires ON email_tokens(expires_at)",
    ],
]


class Database:
    def __init__(self, path: str, busy_timeout_ms: int = 5000):
        self.path = str(path)
        self.busy_timeout_ms = busy_timeout_ms
        self._ready = False
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000,
                               isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_ms)}")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA secure_delete=ON")  # deleted rows are overwritten
        return conn

    def _prepare(self) -> None:
        with self._lock:
            if self._ready:
                return
            path = Path(self.path)
            if not path.exists():
                fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
                os.close(fd)
            os.chmod(self.path, 0o600)
            conn = self._connect()
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                while True:
                    conn.execute("BEGIN IMMEDIATE")
                    try:  # the version is read AFTER taking the write lock
                        version = conn.execute("PRAGMA user_version").fetchone()[0]
                        if version < len(MIGRATIONS):
                            for stmt in MIGRATIONS[version]:
                                conn.execute(stmt)
                            conn.execute(f"PRAGMA user_version={version + 1}")
                        conn.execute("COMMIT")
                    except BaseException:
                        conn.execute("ROLLBACK")
                        raise
                    if version >= len(MIGRATIONS):
                        break
            finally:
                conn.close()
            self._ready = True

    @contextmanager
    def tx(self):
        """Write transaction (BEGIN IMMEDIATE): commit on success, rollback on error."""
        self._prepare()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()
