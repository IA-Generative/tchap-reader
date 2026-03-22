"""SQLite repository for messages and sync state — multi-tenant version."""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# Original schema (v1)
_DB_SCHEMA_V1 = """
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
    room_id TEXT NOT NULL,
    next_batch TEXT,
    last_synced_at INTEGER,
    owner_type TEXT NOT NULL DEFAULT 'global',
    owner_id TEXT NOT NULL DEFAULT 'global',
    PRIMARY KEY (owner_type, owner_id, room_id)
);
"""

# Multi-tenant schema (v2)
_DB_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS matrix_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_type TEXT NOT NULL CHECK(owner_type IN ('user', 'group', 'global')),
    owner_id TEXT NOT NULL,
    homeserver_url TEXT NOT NULL,
    user_id TEXT NOT NULL,
    access_token TEXT NOT NULL,
    device_id TEXT DEFAULT 'OWUI_BOT',
    configured_by TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(owner_type, owner_id)
);

CREATE TABLE IF NOT EXISTS followed_rooms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_type TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    room_name TEXT DEFAULT '',
    added_by TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(owner_type, owner_id, room_id)
);

CREATE TABLE IF NOT EXISTS sso_sessions (
    state TEXT PRIMARY KEY,
    owner_type TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    user_uuid TEXT NOT NULL,
    access_token TEXT,
    matrix_user_id TEXT,
    completed BOOLEAN DEFAULT 0,
    created_at INTEGER NOT NULL
);
"""

# Migration: add owner columns to messages and sync_state
_MIGRATION_ADD_OWNER = """
-- Add owner columns to messages if not present
ALTER TABLE messages ADD COLUMN owner_type TEXT NOT NULL DEFAULT 'global';
ALTER TABLE messages ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'global';

-- Add owner columns to sync_state if not present
ALTER TABLE sync_state ADD COLUMN owner_type TEXT NOT NULL DEFAULT 'global';
ALTER TABLE sync_state ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'global';
"""


class Database:
    """Thread-safe SQLite database for messages and sync state — multi-tenant."""

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
            conn.executescript(_DB_SCHEMA_V1)
            conn.executescript(_DB_SCHEMA_V2)
            self._run_migrations(conn)

    def _run_migrations(self, conn: sqlite3.Connection) -> None:
        """Run schema migrations for multi-tenant support."""
        # Check if messages table already has owner_type column
        cursor = conn.execute("PRAGMA table_info(messages)")
        columns = {row["name"] for row in cursor.fetchall()}

        if "owner_type" not in columns:
            logger.info("Migrating messages table: adding owner_type, owner_id")
            conn.execute("ALTER TABLE messages ADD COLUMN owner_type TEXT NOT NULL DEFAULT 'global'")
            conn.execute("ALTER TABLE messages ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'global'")

        # Check sync_state — need to migrate from single PK (room_id) to composite
        cursor = conn.execute("PRAGMA table_info(sync_state)")
        columns = {row["name"] for row in cursor.fetchall()}

        if "owner_type" not in columns:
            logger.info("Migrating sync_state table to multi-tenant")
            # Recreate table with composite primary key
            conn.execute("ALTER TABLE sync_state RENAME TO sync_state_old")
            conn.execute("""
                CREATE TABLE sync_state (
                    room_id TEXT NOT NULL,
                    next_batch TEXT,
                    last_synced_at INTEGER,
                    owner_type TEXT NOT NULL DEFAULT 'global',
                    owner_id TEXT NOT NULL DEFAULT 'global',
                    PRIMARY KEY (owner_type, owner_id, room_id)
                )
            """)
            conn.execute("""
                INSERT INTO sync_state (room_id, next_batch, last_synced_at, owner_type, owner_id)
                SELECT room_id, next_batch, last_synced_at, 'global', 'global'
                FROM sync_state_old
            """)
            conn.execute("DROP TABLE sync_state_old")
        else:
            # Already migrated — but check if PK is correct (might be old schema with added columns)
            # Try to detect by checking if we can insert two rows for same room_id
            pass

        # Create indexes for multi-tenant queries
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_owner "
            "ON messages(owner_type, owner_id, room_id, timestamp)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_followed_rooms_owner "
            "ON followed_rooms(owner_type, owner_id)"
        )

    # ── Matrix accounts ──────────────────────────────────────────

    def save_matrix_account(
        self,
        owner_type: str,
        owner_id: str,
        homeserver_url: str,
        user_id: str,
        access_token: str,
        device_id: str,
        configured_by: str,
    ) -> None:
        """Save or update a Matrix account for an owner."""
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO matrix_accounts
                (owner_type, owner_id, homeserver_url, user_id, access_token,
                 device_id, configured_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (owner_type, owner_id, homeserver_url, user_id, access_token,
                 device_id, configured_by, int(time.time())),
            )

    def get_matrix_account(self, owner_type: str, owner_id: str) -> dict | None:
        """Get the Matrix account for an owner."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM matrix_accounts WHERE owner_type = ? AND owner_id = ?",
                (owner_type, owner_id),
            ).fetchone()
            return dict(row) if row else None

    def get_all_matrix_accounts(self) -> list[dict]:
        """Get all configured Matrix accounts."""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM matrix_accounts ORDER BY created_at").fetchall()
            return [dict(r) for r in rows]

    def delete_matrix_account(self, owner_type: str, owner_id: str) -> None:
        """Delete a Matrix account and its associated data."""
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM matrix_accounts WHERE owner_type = ? AND owner_id = ?",
                (owner_type, owner_id),
            )
            conn.execute(
                "DELETE FROM followed_rooms WHERE owner_type = ? AND owner_id = ?",
                (owner_type, owner_id),
            )

    # ── Followed rooms ───────────────────────────────────────────

    def follow_room(
        self,
        owner_type: str,
        owner_id: str,
        room_id: str,
        room_name: str,
        added_by: str,
    ) -> bool:
        """Add a room to the followed list. Returns True if added."""
        try:
            with self._connect() as conn:
                conn.execute(
                    """INSERT OR IGNORE INTO followed_rooms
                    (owner_type, owner_id, room_id, room_name, added_by, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (owner_type, owner_id, room_id, room_name, added_by, int(time.time())),
                )
                return conn.total_changes > 0
        except sqlite3.Error as exc:
            logger.error("Failed to follow room: %s", exc)
            return False

    def unfollow_room(self, owner_type: str, owner_id: str, room_id: str) -> bool:
        """Remove a room from the followed list."""
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM followed_rooms WHERE owner_type = ? AND owner_id = ? AND room_id = ?",
                (owner_type, owner_id, room_id),
            )
            return conn.total_changes > 0

    def get_followed_rooms(self, owner_type: str, owner_id: str) -> list[dict]:
        """Get followed rooms for an owner."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM followed_rooms WHERE owner_type = ? AND owner_id = ?",
                (owner_type, owner_id),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_all_followed_room_ids(self, owner_type: str, owner_id: str) -> set[str]:
        """Get set of followed room IDs for an owner."""
        rooms = self.get_followed_rooms(owner_type, owner_id)
        return {r["room_id"] for r in rooms}

    # ── SSO sessions ─────────────────────────────────────────────

    def create_sso_session(
        self, state: str, owner_type: str, owner_id: str, user_uuid: str,
    ) -> None:
        """Create a pending SSO session."""
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO sso_sessions
                (state, owner_type, owner_id, user_uuid, completed, created_at)
                VALUES (?, ?, ?, ?, 0, ?)""",
                (state, owner_type, owner_id, user_uuid, int(time.time())),
            )

    def complete_sso_session(
        self, state: str, access_token: str, matrix_user_id: str,
    ) -> bool:
        """Mark an SSO session as completed with the received token."""
        with self._connect() as conn:
            conn.execute(
                """UPDATE sso_sessions
                SET access_token = ?, matrix_user_id = ?, completed = 1
                WHERE state = ?""",
                (access_token, matrix_user_id, state),
            )
            return conn.total_changes > 0

    def get_sso_session(self, state: str) -> dict | None:
        """Get an SSO session by state."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sso_sessions WHERE state = ?", (state,)
            ).fetchone()
            return dict(row) if row else None

    def cleanup_sso_sessions(self, max_age_seconds: int = 600) -> None:
        """Remove expired SSO sessions."""
        cutoff = int(time.time()) - max_age_seconds
        with self._connect() as conn:
            conn.execute("DELETE FROM sso_sessions WHERE created_at < ?", (cutoff,))

    # ── Messages (multi-tenant) ──────────────────────────────────

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
        owner_type: str = "global",
        owner_id: str = "global",
    ) -> bool:
        """Insert a message. Returns True if inserted, False if duplicate."""
        try:
            with self._connect() as conn:
                conn.execute(
                    """INSERT OR IGNORE INTO messages
                    (event_id, room_id, sender, timestamp, body, event_type,
                     reply_to_event_id, is_edit, replaces_event_id, synced_at,
                     owner_type, owner_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (event_id, room_id, sender, timestamp, body, event_type,
                     reply_to_event_id, is_edit, replaces_event_id, int(time.time()),
                     owner_type, owner_id),
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
            conn.execute(
                """INSERT OR IGNORE INTO messages
                (event_id, room_id, sender, timestamp, body, event_type,
                 is_edit, replaces_event_id, synced_at, owner_type, owner_id)
                SELECT ?, room_id, sender, ?, ?, 'm.text', 1, ?, ?, owner_type, owner_id
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
        owner_type: str | None = None,
        owner_id: str | None = None,
    ) -> list[dict]:
        """Get messages for a room within a time window, optionally filtered by owner."""
        until_ts = until_ts or int(time.time() * 1000)

        if owner_type and owner_id:
            query = """SELECT event_id, sender, timestamp, body, reply_to_event_id, is_edit
                FROM messages
                WHERE room_id = ? AND timestamp >= ? AND timestamp <= ?
                    AND is_redacted = 0 AND is_edit = 0
                    AND owner_type = ? AND owner_id = ?
                ORDER BY timestamp ASC
                LIMIT ?"""
            params = (room_id, since_ts, until_ts, owner_type, owner_id, limit)
        else:
            query = """SELECT event_id, sender, timestamp, body, reply_to_event_id, is_edit
                FROM messages
                WHERE room_id = ? AND timestamp >= ? AND timestamp <= ?
                    AND is_redacted = 0 AND is_edit = 0
                ORDER BY timestamp ASC
                LIMIT ?"""
            params = (room_id, since_ts, until_ts, limit)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def get_message_count(self, room_id: str, owner_type: str | None = None, owner_id: str | None = None) -> int:
        """Get total message count for a room."""
        with self._connect() as conn:
            if owner_type and owner_id:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM messages WHERE room_id = ? AND is_redacted = 0 AND owner_type = ? AND owner_id = ?",
                    (room_id, owner_type, owner_id),
                ).fetchone()
            else:
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
                    s.owner_type, s.owner_id,
                    COUNT(m.event_id) as message_count
                FROM sync_state s
                LEFT JOIN messages m ON m.room_id = s.room_id AND m.is_redacted = 0
                    AND m.owner_type = s.owner_type AND m.owner_id = s.owner_id
                GROUP BY s.room_id, s.owner_type, s.owner_id"""
            ).fetchall()
            return [dict(r) for r in rows]

    # -- Sync state (multi-tenant) --

    def get_next_batch(self, room_id: str, owner_type: str = "global", owner_id: str = "global") -> str | None:
        """Get the next_batch token for a room+owner."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT next_batch FROM sync_state WHERE room_id = ? AND owner_type = ? AND owner_id = ?",
                (room_id, owner_type, owner_id),
            ).fetchone()
            return row["next_batch"] if row else None

    def save_next_batch(self, room_id: str, next_batch: str, owner_type: str = "global", owner_id: str = "global") -> None:
        """Save the next_batch token for a room+owner."""
        with self._connect() as conn:
            # Check if exists first (since sync_state PK is room_id alone in v1)
            existing = conn.execute(
                "SELECT 1 FROM sync_state WHERE room_id = ? AND owner_type = ? AND owner_id = ?",
                (room_id, owner_type, owner_id),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE sync_state SET next_batch = ?, last_synced_at = ? WHERE room_id = ? AND owner_type = ? AND owner_id = ?",
                    (next_batch, int(time.time()), room_id, owner_type, owner_id),
                )
            else:
                conn.execute(
                    """INSERT INTO sync_state (room_id, next_batch, last_synced_at, owner_type, owner_id)
                    VALUES (?, ?, ?, ?, ?)""",
                    (room_id, next_batch, int(time.time()), owner_type, owner_id),
                )

    def get_last_synced(self, room_id: str, owner_type: str = "global", owner_id: str = "global") -> int | None:
        """Get the last sync timestamp for a room+owner."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_synced_at FROM sync_state WHERE room_id = ? AND owner_type = ? AND owner_id = ?",
                (room_id, owner_type, owner_id),
            ).fetchone()
            return row["last_synced_at"] if row else None

    # -- Config persistence (legacy, kept for backward compat) --

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
