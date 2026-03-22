"""Tests for FastAPI endpoints — backward compatibility."""

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
    def test_messages_returns_empty(self, test_client):
        """Messages endpoint returns empty list for room with no stored messages."""
        r = test_client.post("/messages", json={"room_id": ROOM_ID, "since_hours": 1})
        assert r.status_code == 200
        assert "messages" in r.json()

    def test_messages_unknown_room_returns_empty(self, test_client):
        """Unknown room returns empty messages (no stored data)."""
        r = test_client.post("/messages", json={"room_id": "!unknown:other"})
        assert r.status_code == 200
        assert r.json()["messages"] == []


class TestLegacyAdminEndpoints:
    def test_admin_status(self, test_client):
        r = test_client.get("/admin/status")
        assert r.status_code == 200
        assert "configured" in r.json()
