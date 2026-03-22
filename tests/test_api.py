"""Tests for FastAPI endpoints."""

import pytest
from tests.mock_data import ROOM_ID


class TestHealthEndpoint:
    def test_healthz(self, test_client):
        r = test_client.get("/healthz")
        assert r.status_code == 200
        assert r.json()["status"] in ("healthy", "unhealthy")


class TestRoomsEndpoint:
    def test_rooms_returns_list(self, test_client):
        r = test_client.get("/rooms")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


class TestMessagesEndpoint:
    def test_messages_allowed_room(self, test_client):
        r = test_client.post("/messages", json={"room_id": ROOM_ID, "since_hours": 1})
        assert r.status_code == 200
        assert "messages" in r.json()

    def test_messages_forbidden_room(self, test_client):
        r = test_client.post("/messages", json={"room_id": "!forbidden:other"})
        assert r.status_code == 403


class TestSyncEndpoint:
    def test_sync_forbidden_room(self, test_client):
        r = test_client.post("/sync", json={"room_id": "!forbidden:other"})
        assert r.status_code == 403
