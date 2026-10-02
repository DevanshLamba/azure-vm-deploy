"""Tiny SQLite data layer (stdlib only). Every query uses parameters, never string formatting.

Every task query is scoped to a user_id: there is no function that reads or changes another
user's tasks. Schema changes are versioned with PRAGMA user_version (see MIGRATIONS).
"""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .schemas import COLORS

MIGRATIONS = {
    # v1: the original single-board schema.
    1: """
    CREATE TABLE IF NOT EXISTS tasks (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        title       TEXT    NOT NULL,
        note        TEXT,
        priority    TEXT    NOT NULL CHECK (priority IN ('low', 'medium', 'high')),
        due_date    TEXT,
        color       TEXT    NOT NULL,
        done        INTEGER NOT NULL DEFAULT 0,
        created_at  TEXT    NOT NULL,
        updated_at  TEXT    NOT NULL
    );
    """,
    # v2: invite-only accounts. Tasks get an owner; the old ownerless tasks (sample data from
    # the public single-board version) are deleted, as decided for this project.
    2: """
    CREATE TABLE users (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
        role          TEXT    NOT NULL CHECK (role IN ('admin', 'user', 'demo')),
        password_hash TEXT    NOT NULL,
        created_at    TEXT    NOT NULL
    );
    CREATE TABLE sessions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        token_hash  TEXT    NOT NULL UNIQUE,
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        csrf_token  TEXT    NOT NULL,
        created_at  INTEGER NOT NULL,
        expires_at  INTEGER NOT NULL
    );
    CREATE INDEX idx_sessions_user ON sessions(user_id);
    ALTER TABLE tasks ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE;
    DELETE FROM tasks WHERE user_id IS NULL;
    CREATE INDEX idx_tasks_user ON tasks(user_id);
    """,
}
SCHEMA_VERSION = max(MIGRATIONS)
FIELDS = ("title", "note", "priority", "due_date", "color", "done")


def db_path() -> str:
    return os.environ.get("DB_PATH", str(Path(__file__).resolve().parent.parent / "data" / "cloudtasks.db"))


@contextmanager
def connect():
    conn = sqlite3.connect(db_path(), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # needed for ON DELETE CASCADE
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    Path(db_path()).parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version == 0 and conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'"
        ).fetchone():
            version = 1  # database from before versioning existed
        for v in range(version + 1, SCHEMA_VERSION + 1):
            conn.executescript(f"BEGIN; {MIGRATIONS[v]} PRAGMA user_version = {v}; COMMIT;")


def schema_version() -> int:
    with connect() as conn:
        return conn.execute("PRAGMA user_version").fetchone()[0]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _task(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["done"] = bool(d["done"])
    d.pop("user_id", None)
    return d


def ping() -> bool:
    with connect() as conn:
        return conn.execute("SELECT 1").fetchone()[0] == 1


# ---------------------------------------------------------------- tasks (always per user)
def count_tasks(user_id: int) -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ?", (user_id,)).fetchone()[0]


def list_tasks(user_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM tasks WHERE user_id = ? ORDER BY id DESC", (user_id,)).fetchall()
    return [_task(r) for r in rows]


def get_task(user_id: int, task_id: int) -> dict | None:
    with connect() as conn:
        r = conn.execute("SELECT * FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id)).fetchone()
    return _task(r) if r else None


def create_task(user_id: int, data: dict) -> dict:
    now = _now()
    with connect() as conn:
        if not data.get("color"):
            # Rotate through the pastel palette so neighbouring cards differ.
            n = conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ?", (user_id,)).fetchone()[0]
            data["color"] = COLORS[n % len(COLORS)]
        cur = conn.execute(
            "INSERT INTO tasks (user_id, title, note, priority, due_date, color, done, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, data["title"], data.get("note"), data["priority"], data.get("due_date"), data["color"],
             int(bool(data.get("done"))), now, now),
        )
        task_id = cur.lastrowid
    return get_task(user_id, task_id)


def update_task(user_id: int, task_id: int, changes: dict) -> dict | None:
    changes = {k: v for k, v in changes.items() if k in FIELDS}
    if not changes:
        return get_task(user_id, task_id)
    changes["updated_at"] = _now()
    # Column names come from the FIELDS whitelist above, values are bound parameters.
    assignments = ", ".join(f"{col} = ?" for col in changes)
    with connect() as conn:
        cur = conn.execute(
            f"UPDATE tasks SET {assignments} WHERE id = ? AND user_id = ?", (*changes.values(), task_id, user_id)
        )
        if cur.rowcount == 0:
            return None
    return get_task(user_id, task_id)


def toggle_task(user_id: int, task_id: int) -> dict | None:
    with connect() as conn:
        cur = conn.execute(
            "UPDATE tasks SET done = 1 - done, updated_at = ? WHERE id = ? AND user_id = ?", (_now(), task_id, user_id)
        )
        if cur.rowcount == 0:
            return None
    return get_task(user_id, task_id)


def delete_task(user_id: int, task_id: int) -> bool:
    with connect() as conn:
        return conn.execute("DELETE FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id)).rowcount > 0


# ---------------------------------------------------------------- users
def create_user(username: str, role: str, password_hash: str) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, role, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (username, role, password_hash, _now()),
        )
        return cur.lastrowid


def get_user_by_name(username: str) -> dict | None:
    with connect() as conn:
        r = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return dict(r) if r else None


def list_users() -> list[dict]:
    """Public fields only: the password hash is never selected here."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT u.username, u.role, u.created_at, COUNT(t.id) AS tasks"
            " FROM users u LEFT JOIN tasks t ON t.user_id = u.id GROUP BY u.id ORDER BY u.id"
        ).fetchall()
    return [dict(r) for r in rows]


def set_password_hash(user_id: int, password_hash: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))  # log out everywhere


def delete_user(user_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))  # tasks + sessions cascade


# ---------------------------------------------------------------- sessions
def create_session(token_hash: str, user_id: int, csrf_token: str, now: int, expires_at: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))  # housekeeping
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, csrf_token, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            (token_hash, user_id, csrf_token, now, expires_at),
        )


def get_session(token_hash: str) -> dict | None:
    with connect() as conn:
        r = conn.execute(
            "SELECT s.id AS session_id, s.csrf_token, s.expires_at, u.id AS user_id, u.username, u.role"
            " FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
            (token_hash,),
        ).fetchone()
    return dict(r) if r else None


def delete_session(token_hash: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
