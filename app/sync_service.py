"""Incremental sync service — fetches messages from Matrix and stores them (multi-tenant)."""

from __future__ import annotations

import logging

from app.database import Database
from app.matrix_client import MatrixClient

logger = logging.getLogger(__name__)


class SyncService:
    """Manages incremental sync of Matrix rooms — multi-tenant aware."""

    def __init__(self, db: Database, client: MatrixClient):
        self._db = db
        self._client = client

    async def sync_room(
        self,
        room_id: str,
        owner_type: str = "global",
        owner_id: str = "global",
        allowed_rooms: set[str] | None = None,
    ) -> dict:
        """Sync a single room incrementally. Returns sync stats.

        If allowed_rooms is provided, checks that room_id is in the set.
        """
        if allowed_rooms is not None and room_id not in allowed_rooms:
            raise PermissionError(f"Room {room_id} is not in the allowlist")

        since = self._db.get_next_batch(room_id, owner_type, owner_id)
        logger.info("Syncing room %s (owner=%s/%s, since=%s)",
                     room_id, owner_type, owner_id, since or "initial")

        data = await self._client.sync(
            since=since,
            room_ids=[room_id],
            timeout_ms=0,
        )

        next_batch = data.get("next_batch", "")
        rooms = data.get("rooms", {}).get("join", {})
        room_data = rooms.get(room_id, {})
        timeline = room_data.get("timeline", {})
        events = timeline.get("events", [])

        new_count = 0
        for event in events:
            processed = self._process_event(event, room_id, owner_type, owner_id)
            if processed:
                new_count += 1

        if next_batch:
            self._db.save_next_batch(room_id, next_batch, owner_type, owner_id)

        total = self._db.get_message_count(room_id, owner_type, owner_id)
        logger.info("Room %s (owner=%s/%s): %d new messages (total: %d)",
                     room_id, owner_type, owner_id, new_count, total)

        return {
            "room_id": room_id,
            "synced": len(events),
            "new_messages": new_count,
            "next_batch": next_batch,
        }

    def _process_event(
        self, event: dict, room_id: str,
        owner_type: str = "global", owner_id: str = "global",
    ) -> bool:
        """Process a single Matrix event. Returns True if a message was stored."""
        event_type = event.get("type", "")
        event_id = event.get("event_id", "")
        sender = event.get("sender", "")
        origin_ts = event.get("origin_server_ts", 0)
        content = event.get("content", {})

        # Handle redactions
        if event_type == "m.room.redaction":
            redacted_id = event.get("redacts", "")
            if redacted_id:
                self._db.mark_redacted(redacted_id)
                logger.debug("Redacted event %s", redacted_id)
            return False

        if event_type != "m.room.message":
            return False

        msgtype = content.get("msgtype", "")
        if msgtype != "m.text":
            return False

        body = content.get("body", "").strip()
        if not body:
            return False

        # Check for edit (m.replace)
        relates_to = content.get("m.relates_to", {})
        if relates_to.get("rel_type") == "m.replace":
            original_id = relates_to.get("event_id", "")
            new_content = content.get("m.new_content", {})
            new_body = new_content.get("body", body)
            if original_id:
                self._db.apply_edit(original_id, new_body, event_id)
                logger.debug("Applied edit %s → %s", event_id, original_id)
                return True

        # Check for reply
        reply_to = None
        in_reply_to = relates_to.get("m.in_reply_to", {})
        if in_reply_to:
            reply_to = in_reply_to.get("event_id")
            # Strip reply fallback (lines starting with > )
            lines = body.split("\n")
            cleaned = []
            past_fallback = False
            for line in lines:
                if line.startswith("> ") and not past_fallback:
                    continue
                if not line.strip() and not past_fallback:
                    past_fallback = True
                    continue
                past_fallback = True
                cleaned.append(line)
            body = "\n".join(cleaned).strip() or body

        return self._db.insert_message(
            event_id=event_id,
            room_id=room_id,
            sender=sender,
            timestamp=origin_ts,
            body=body,
            reply_to_event_id=reply_to,
            owner_type=owner_type,
            owner_id=owner_id,
        )
