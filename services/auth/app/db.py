"""SQLite storage: users, login attempts and revoked tokens."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import settings

ROLES = ("admin", "viewer", "service")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL CHECK (role IN ('admin', 'viewer', 'service')),
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS login_attempts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username   TEXT    NOT NULL,
    client_ip  TEXT    NOT NULL,
    method     TEXT    NOT NULL,          -- form | api
    success    INTEGER NOT NULL,
    reason     TEXT,                      -- bad_credentials | inactive | throttled
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS login_attempts_lookup
    ON login_attempts (client_ip, username, success, created_at);

CREATE TABLE IF NOT EXISTS revoked_tokens (
    jti        TEXT PRIMARY KEY,
    expires_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    conn = sqlite3.connect(settings.db_path, timeout=5, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        # Connection-level execute is not traced: keeps PRAGMA noise out of traces
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.cursor().execute("PRAGMA journal_mode = WAL")
        conn.cursor().executescript(SCHEMA)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")
        conn.cursor().execute("DELETE FROM login_attempts WHERE created_at < ?", (cutoff,))
        conn.cursor().execute("DELETE FROM revoked_tokens WHERE expires_at < ?", (now_iso(),))


# ---------------------------------------------------------------- users
def get_user(conn: sqlite3.Connection, username: str) -> sqlite3.Row | None:
    return conn.cursor().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


def list_users(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.cursor().execute(
        "SELECT id, username, role, is_active, created_at, updated_at FROM users ORDER BY id").fetchall()


def create_user(conn: sqlite3.Connection, username: str, password_hash: str, role: str) -> None:
    ts = now_iso()
    conn.cursor().execute(
        "INSERT INTO users (username, password_hash, role, is_active, created_at, updated_at) "
        "VALUES (?, ?, ?, 1, ?, ?)", (username, password_hash, role, ts, ts))


def update_user(conn: sqlite3.Connection, username: str, **fields) -> bool:
    if not fields:
        return get_user(conn, username) is not None
    sets = ", ".join(f"{k} = ?" for k in fields) + ", updated_at = ?"
    cur = conn.cursor().execute(f"UPDATE users SET {sets} WHERE username = ?",
                       (*fields.values(), now_iso(), username))
    return cur.rowcount > 0


def delete_user(conn: sqlite3.Connection, username: str) -> bool:
    return conn.cursor().execute("DELETE FROM users WHERE username = ?", (username,)).rowcount > 0


def count_by_role(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.cursor().execute("SELECT role, count(*) AS n FROM users WHERE is_active = 1 GROUP BY role").fetchall()
    return {r["role"]: r["n"] for r in rows}


# -------------------------------------------------------- login attempts
def record_attempt(conn: sqlite3.Connection, username: str, ip: str, method: str,
                   success: bool, reason: str | None) -> None:
    conn.cursor().execute(
        "INSERT INTO login_attempts (username, client_ip, method, success, reason, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)", (username, ip, method, int(success), reason, now_iso()))


def recent_failures(conn: sqlite3.Connection, username: str, ip: str, minutes: int) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    return conn.cursor().execute(
        "SELECT count(*) FROM login_attempts "
        "WHERE client_ip = ? AND username = ? AND success = 0 AND created_at >= ?",
        (ip, username, cutoff)).fetchone()[0]


# --------------------------------------------------------- revocation
def revoke(conn: sqlite3.Connection, jti: str, expires_at: datetime) -> None:
    conn.cursor().execute("INSERT OR IGNORE INTO revoked_tokens (jti, expires_at) VALUES (?, ?)",
                 (jti, expires_at.isoformat(timespec="seconds")))


def is_revoked(conn: sqlite3.Connection, jti: str) -> bool:
    return conn.cursor().execute("SELECT 1 FROM revoked_tokens WHERE jti = ?", (jti,)).fetchone() is not None
