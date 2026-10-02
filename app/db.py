"""Tiny SQLite data layer (stdlib only). Every query uses parameters, never string formatting."""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .schemas import COLORS

SCHEMA = """
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
"""

FIELDS = ("title", "note", "priority", "due_date", "color", "done")


def db_path() -> str:
    return os.environ.get("DB_PATH", str(Path(__file__).resolve().parent.parent / "data" / "cloudtasks.db"))


@contextmanager
def connect():
    conn = sqlite3.connect(db_path(), timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    Path(db_path()).parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["done"] = bool(d["done"])
    return d


def ping() -> bool:
    with connect() as conn:
        return conn.execute("SELECT 1").fetchone()[0] == 1


def count_tasks() -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]


def list_tasks() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall()
    return [_row(r) for r in rows]


def get_task(task_id: int) -> dict | None:
    with connect() as conn:
        r = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return _row(r) if r else None


def create_task(data: dict) -> dict:
    now = _now()
    with connect() as conn:
        if not data.get("color"):
            # Rotate through the pastel palette so neighbouring cards differ.
            last = conn.execute("SELECT COALESCE(MAX(id), 0) FROM tasks").fetchone()[0]
            data["color"] = COLORS[last % len(COLORS)]
        cur = conn.execute(
            "INSERT INTO tasks (title, note, priority, due_date, color, done, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
            (data["title"], data.get("note"), data["priority"], data.get("due_date"), data["color"], now, now),
        )
        task_id = cur.lastrowid
    return get_task(task_id)


def update_task(task_id: int, changes: dict) -> dict | None:
    changes = {k: v for k, v in changes.items() if k in FIELDS}
    if not changes:
        return get_task(task_id)
    changes["updated_at"] = _now()
    # Column names come from the FIELDS whitelist above, values are bound parameters.
    assignments = ", ".join(f"{col} = ?" for col in changes)
    with connect() as conn:
        cur = conn.execute(f"UPDATE tasks SET {assignments} WHERE id = ?", (*changes.values(), task_id))
        if cur.rowcount == 0:
            return None
    return get_task(task_id)


def toggle_task(task_id: int) -> dict | None:
    with connect() as conn:
        cur = conn.execute(
            "UPDATE tasks SET done = 1 - done, updated_at = ? WHERE id = ?", (_now(), task_id)
        )
        if cur.rowcount == 0:
            return None
    return get_task(task_id)


def delete_task(task_id: int) -> bool:
    with connect() as conn:
        return conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,)).rowcount > 0
