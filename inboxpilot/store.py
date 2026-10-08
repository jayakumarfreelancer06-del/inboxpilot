"""App database: incoming messages, per-step audit events, the outbox and the dead-letter queue (status = dead_letter)."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from typing import Any

from inboxpilot.config import get_settings

_lock = threading.Lock()

STATUSES = ("queued", "processing", "awaiting_review", "sent", "rejected", "archived", "dead_letter")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(get_settings().db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init() -> None:
    get_settings().db_path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, closing(connect()) as conn, conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY, source TEXT NOT NULL, external_id TEXT, thread_id TEXT,
                sender TEXT NOT NULL, sender_name TEXT, subject TEXT, body TEXT NOT NULL, received_at TEXT,
                status TEXT NOT NULL DEFAULT 'queued', intent TEXT, confidence REAL,
                attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, next_attempt_at TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS messages_external ON messages (source, external_id);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT NOT NULL, at TEXT NOT NULL,
                step TEXT NOT NULL, status TEXT NOT NULL, duration_ms INTEGER, detail TEXT
            );
            CREATE INDEX IF NOT EXISTS events_message ON events (message_id, id);
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT NOT NULL, channel TEXT NOT NULL, recipient TEXT NOT NULL,
                subject TEXT, body TEXT NOT NULL, attachments TEXT, edited INTEGER NOT NULL DEFAULT 0,
                reviewer TEXT, sent_at TEXT NOT NULL, delivery TEXT
            );
            """
        )


def add_message(*, id: str, source: str, sender: str, body: str, subject: str | None = None, sender_name: str | None = None,
                external_id: str | None = None, thread_id: str | None = None, received_at: str | None = None) -> bool:
    """Insert a new message. Returns False if this external message was already ingested."""
    with _lock, closing(connect()) as conn, conn:
        try:
            conn.execute(
                """INSERT INTO messages (id, source, external_id, thread_id, sender, sender_name, subject, body, received_at, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (id, source, external_id or id, thread_id, sender.lower(), sender_name, subject, body, received_at or _now(), _now(), _now()),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def update(message_id: str, **fields: Any) -> None:
    fields["updated_at"] = _now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _lock, closing(connect()) as conn, conn:
        conn.execute(f"UPDATE messages SET {cols} WHERE id = ?", (*fields.values(), message_id))


def get(message_id: str) -> dict | None:
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        return dict(row) if row else None


def list_messages(status: str | None = None, limit: int = 200) -> list[dict]:
    with closing(connect()) as conn:
        if status:
            rows = conn.execute("SELECT * FROM messages WHERE status = ? ORDER BY received_at DESC LIMIT ?", (status, limit))
        else:
            rows = conn.execute("SELECT * FROM messages ORDER BY received_at DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]


def due_messages(limit: int = 10) -> list[dict]:
    """Queued messages whose retry backoff (if any) has elapsed."""
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE status = 'queued' AND (next_attempt_at IS NULL OR next_attempt_at <= ?) ORDER BY received_at LIMIT ?",
            (_now(), limit),
        )
        return [dict(r) for r in rows]


def requeue_interrupted() -> int:
    """After a crash/restart, messages that were mid-run go back to the queue (the checkpointer resumes them)."""
    with _lock, closing(connect()) as conn, conn:
        return conn.execute("UPDATE messages SET status = 'queued' WHERE status = 'processing'").rowcount


def add_event(message_id: str, step: str, status: str, duration_ms: int | None = None, detail: Any = None) -> None:
    with _lock, closing(connect()) as conn, conn:
        conn.execute(
            "INSERT INTO events (message_id, at, step, status, duration_ms, detail) VALUES (?,?,?,?,?,?)",
            (message_id, _now(), step, status, duration_ms, json.dumps(detail, default=str) if detail is not None else None),
        )


def events(message_id: str) -> list[dict]:
    with closing(connect()) as conn:
        rows = conn.execute("SELECT * FROM events WHERE message_id = ? ORDER BY id", (message_id,))
        return [{**dict(r), "detail": json.loads(r["detail"]) if r["detail"] else None} for r in rows]


def add_outbox(*, message_id: str, channel: str, recipient: str, subject: str | None, body: str, attachments: list[str],
               edited: bool, reviewer: str | None, delivery: str) -> None:
    with _lock, closing(connect()) as conn, conn:
        conn.execute(
            """INSERT INTO outbox (message_id, channel, recipient, subject, body, attachments, edited, reviewer, sent_at, delivery)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (message_id, channel, recipient, subject, body, json.dumps(attachments), int(edited), reviewer, _now(), delivery),
        )


def outbox(message_id: str | None = None) -> list[dict]:
    with closing(connect()) as conn:
        q, args = ("SELECT * FROM outbox WHERE message_id = ? ORDER BY id", (message_id,)) if message_id else ("SELECT * FROM outbox ORDER BY id DESC LIMIT 100", ())
        return [{**dict(r), "attachments": json.loads(r["attachments"] or "[]")} for r in conn.execute(q, args)]


def stats() -> dict:
    with closing(connect()) as conn:
        counts = {r["status"]: r["n"] for r in conn.execute("SELECT status, count(*) AS n FROM messages GROUP BY status")}
        auto = conn.execute("SELECT count(*) FROM outbox WHERE edited = 0").fetchone()[0]
        edited = conn.execute("SELECT count(*) FROM outbox WHERE edited = 1").fetchone()[0]
        return {"by_status": {s: counts.get(s, 0) for s in STATUSES}, "sent_unedited": auto, "sent_edited": edited}
