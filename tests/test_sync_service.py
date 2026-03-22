"""Tests for sync service — event processing logic (multi-tenant)."""

import pytest
from unittest.mock import AsyncMock

from app.sync_service import SyncService
from tests.mock_data import (
    ROOM_ID,
    SYNC_RESPONSE_WITH_MESSAGES,
    SYNC_RESPONSE_EMPTY,
    SYNC_RESPONSE_WITH_REDACTION,
)

ALLOWED_ROOMS = {ROOM_ID}


class TestSyncService:
    @pytest.fixture
    def service(self, tmp_db):
        client = AsyncMock()
        return SyncService(tmp_db, client), tmp_db, client

    @pytest.mark.asyncio
    async def test_sync_stores_messages(self, service):
        svc, db, client = service
        client.sync.return_value = SYNC_RESPONSE_WITH_MESSAGES
        result = await svc.sync_room(ROOM_ID, allowed_rooms=ALLOWED_ROOMS)
        assert result["new_messages"] > 0
        assert db.get_message_count(ROOM_ID) > 0

    @pytest.mark.asyncio
    async def test_sync_saves_next_batch(self, service):
        svc, db, client = service
        client.sync.return_value = SYNC_RESPONSE_WITH_MESSAGES
        await svc.sync_room(ROOM_ID, allowed_rooms=ALLOWED_ROOMS)
        assert db.get_next_batch(ROOM_ID) == "s123_456"

    @pytest.mark.asyncio
    async def test_sync_empty(self, service):
        svc, db, client = service
        client.sync.return_value = SYNC_RESPONSE_EMPTY
        result = await svc.sync_room(ROOM_ID, allowed_rooms=ALLOWED_ROOMS)
        assert result["new_messages"] == 0

    @pytest.mark.asyncio
    async def test_sync_handles_redaction(self, service):
        svc, db, client = service
        client.sync.return_value = SYNC_RESPONSE_WITH_REDACTION
        await svc.sync_room(ROOM_ID, allowed_rooms=ALLOWED_ROOMS)
        msgs = db.get_messages(ROOM_ID, since_ts=0)
        assert all(m["body"] != "Message à supprimer" for m in msgs)

    @pytest.mark.asyncio
    async def test_sync_disallowed_room(self, service):
        svc, _, _ = service
        with pytest.raises(PermissionError):
            await svc.sync_room("!forbidden:other.server", allowed_rooms=ALLOWED_ROOMS)

    @pytest.mark.asyncio
    async def test_sync_no_allowlist(self, service):
        """When allowed_rooms is None, all rooms are allowed."""
        svc, db, client = service
        client.sync.return_value = SYNC_RESPONSE_WITH_MESSAGES
        result = await svc.sync_room(ROOM_ID, allowed_rooms=None)
        assert result["new_messages"] > 0

    @pytest.mark.asyncio
    async def test_sync_handles_edits(self, service):
        svc, db, client = service
        client.sync.return_value = SYNC_RESPONSE_WITH_MESSAGES
        await svc.sync_room(ROOM_ID, allowed_rooms=ALLOWED_ROOMS)
        msgs = db.get_messages(ROOM_ID, since_ts=0)
        evt5 = [m for m in msgs if m["event_id"] == "$evt5"]
        if evt5:
            assert "5 fois par jour" in evt5[0]["body"]

    @pytest.mark.asyncio
    async def test_sync_with_owner(self, service):
        """Sync with a specific owner stores messages with that owner."""
        svc, db, client = service
        client.sync.return_value = SYNC_RESPONSE_WITH_MESSAGES
        result = await svc.sync_room(
            ROOM_ID,
            owner_type="user",
            owner_id="u1",
            allowed_rooms=ALLOWED_ROOMS,
        )
        assert result["new_messages"] > 0

        # Messages should be stored with the owner
        msgs = db.get_messages(ROOM_ID, since_ts=0, owner_type="user", owner_id="u1")
        assert len(msgs) > 0

        # No messages for default owner
        global_msgs = db.get_messages(ROOM_ID, since_ts=0, owner_type="global", owner_id="global")
        assert len(global_msgs) == 0
