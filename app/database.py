"""SQLite repository for messages and sync state."""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    event_id TEXT PRIMARY KEY,
    room_id TEXT NOT NULL,
    sender TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    body TEXT NOT NULL,
    event_type TEXT DEFAULT 'm.text',
    reply_to_event_id TEXT,
    is_edit BOOLEAN DEFAULT 0,
    replaces_event_id TEXT,
    is_redacted BOOLEAN DEFAULT 0,
    synced_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_room_ts ON messages(room_id, timestamp);

CREATE TABLE IF NOT EXISTS sync_state (
    room_id TEXT PRIMARY KEY,
    next_batch TEXT,
    last_synced_at INTEGER
);
"""


class Database:
    """Thread-safe SQLite database for messages and sync state."""

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or settings.TCHAP_STORE_PATH
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_DB_SCHEMA)

    def insert_message(
        self,
        event_id: str,
        room_id: str,
        sender: str,
        timestamp: int,
        body: str,
        event_type: str = "m.text",
        reply_to_event_id: str | None = None,
        is_edit: bool = False,
        replaces_event_id: str | None = None,
    ) -> bool:
        """Insert a message. Returns True if inserted, False if duplicate."""
        try:
            with self._connect() as conn:
                conn.execute(
                    """INSERT OR IGNORE INTO messages
                    (event_id, room_id, sender, timestamp, body, event_type,
                     reply_to_event_id, is_edit, replaces_event_id, synced_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (event_id, room_id, sender, timestamp, body, event_type,
                     reply_to_event_id, is_edit, replaces_event_id, int(time.time())),
                )
                return conn.total_changes > 0
        except sqlite3.Error as exc:
            logger.error("Failed to insert message %s: %s", event_id, exc)
            return False

    def mark_redacted(self, event_id: str) -> None:
        """Mark a message as redacted."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE messages SET is_redacted = 1, body = '[redacted]' WHERE event_id = ?",
                (event_id,),
            )

    def apply_edit(self, original_event_id: str, new_body: str, edit_event_id: str) -> None:
        """Apply an edit to an existing message."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE messages SET body = ? WHERE event_id = ?",
                (new_body, original_event_id),
            )
            # Also store the edit event
            conn.execute(
                """INSERT OR IGNORE INTO messages
                (event_id, room_id, sender, timestamp, body, event_type,
                 is_edit, replaces_event_id, synced_at)
                SELECT ?, room_id, sender, ?, ?, 'm.text', 1, ?, ?
                FROM messages WHERE event_id = ?""",
                (edit_event_id, int(time.time()), new_body, original_event_id,
                 int(time.time()), original_event_id),
            )

    def get_messages(
        self,
        room_id: str,
        since_ts: int,
        until_ts: int | None = None,
        limit: int = 1000,
    ) -> list[dict]:
        """Get messages for a room within a time window."""
        until_ts = until_ts or int(time.time() * 1000)
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT event_id, sender, timestamp, body, reply_to_event_id, is_edit
                FROM messages
                WHERE room_id = ? AND timestamp >= ? AND timestamp <= ?
                    AND is_redacted = 0 AND is_edit = 0
                ORDER BY timestamp ASC
                LIMIT ?""",
                (room_id, since_ts, until_ts, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_message_count(self, room_id: str) -> int:
        """Get total message count for a room."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM messages WHERE room_id = ? AND is_redacted = 0",
                (room_id,),
            ).fetchone()
            return row["cnt"] if row else 0

    def get_total_messages(self) -> int:
        """Get total message count across all rooms."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM messages WHERE is_redacted = 0"
            ).fetchone()
            return row["cnt"] if row else 0

    def get_tracked_rooms(self) -> list[dict]:
        """Get rooms with sync state."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT s.room_id, s.next_batch, s.last_synced_at,
                    COUNT(m.event_id) as message_count
                FROM sync_state s
                LEFT JOIN messages m ON m.room_id = s.room_id AND m.is_redacted = 0
                GROUP BY s.room_id"""
            ).fetchall()
            return [dict(r) for r in rows]

    # -- Sync state --

    def get_next_batch(self, room_id: str) -> str | None:
        """Get the next_batch token for a room."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT next_batch FROM sync_state WHERE room_id = ?",
                (room_id,),
            ).fetchone()
            return row["next_batch"] if row else None

    def save_next_batch(self, room_id: str, next_batch: str) -> None:
        """Save the next_batch token for a room."""
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO sync_state (room_id, next_batch, last_synced_at)
                VALUES (?, ?, ?)""",
                (room_id, next_batch, int(time.time())),
            )

    def get_last_synced(self, room_id: str) -> int | None:
        """Get the last sync timestamp for a room."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_synced_at FROM sync_state WHERE room_id = ?",
                (room_id,),
            ).fetchone()
            return row["last_synced_at"] if row else None

    # -- Config persistence --

    def set_config(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                (key, value),
            )

    def get_config(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM config WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else None

    def get_all_config(self) -> dict[str, str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM config").fetchall()
            return {r["key"]: r["value"] for r in rows}
