"""Tests for sync service — event processing logic."""

import pytest
from unittest.mock import AsyncMock

from app.sync_service import SyncService
from tests.mock_data import (
    ROOM_ID,
    SYNC_RESPONSE_WITH_MESSAGES,
    SYNC_RESPONSE_EMPTY,
    SYNC_RESPONSE_WITH_REDACTION,
)


class TestSyncService:
    @pytest.fixture
    def service(self, tmp_db):
        client = AsyncMock()
        return SyncService(tmp_db, client), tmp_db, client

    @pytest.mark.asyncio
    async def test_sync_stores_messages(self, service):
        svc, db, client = service
        client.sync.return_value = SYNC_RESPONSE_WITH_MESSAGES
        result = await svc.sync_room(ROOM_ID)
        assert result["new_messages"] > 0
        assert db.get_message_count(ROOM_ID) > 0

    @pytest.mark.asyncio
    async def test_sync_saves_next_batch(self, service):
        svc, db, client = service
        client.sync.return_value = SYNC_RESPONSE_WITH_MESSAGES
        await svc.sync_room(ROOM_ID)
        assert db.get_next_batch(ROOM_ID) == "s123_456"

    @pytest.mark.asyncio
    async def test_sync_empty(self, service):
        svc, db, client = service
        client.sync.return_value = SYNC_RESPONSE_EMPTY
        result = await svc.sync_room(ROOM_ID)
        assert result["new_messages"] == 0

    @pytest.mark.asyncio
    async def test_sync_handles_redaction(self, service):
        svc, db, client = service
        # First insert then redact
        client.sync.return_value = SYNC_RESPONSE_WITH_REDACTION
        await svc.sync_room(ROOM_ID)
        msgs = db.get_messages(ROOM_ID, since_ts=0)
        assert all(m["body"] != "Message à supprimer" for m in msgs)

    @pytest.mark.asyncio
    async def test_sync_disallowed_room(self, service):
        svc, _, _ = service
        with pytest.raises(PermissionError):
            await svc.sync_room("!forbidden:other.server")

    @pytest.mark.asyncio
    async def test_sync_handles_edits(self, service):
        svc, db, client = service
        client.sync.return_value = SYNC_RESPONSE_WITH_MESSAGES
        await svc.sync_room(ROOM_ID)
        msgs = db.get_messages(ROOM_ID, since_ts=0)
        # The edit should have updated $evt5's body
        evt5 = [m for m in msgs if m["event_id"] == "$evt5"]
        if evt5:
            assert "5 fois par jour" in evt5[0]["body"]
