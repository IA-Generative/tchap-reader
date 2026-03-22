"""Tests for multi-tenant API endpoints."""

import time
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
    def test_messages_with_owner(self, test_client):
        r = test_client.post("/messages", json={
            "room_id": ROOM_ID,
            "since_hours": 1,
            "owner_type": "global",
            "owner_id": "global",
        })
        assert r.status_code == 200
        assert "messages" in r.json()


class TestFollowedRooms:
    def test_follow_and_unfollow(self, tmp_db):
        # Follow a room
        ok = tmp_db.follow_room("user", "u1", "!room:s", "Test Room", "u1")
        assert ok is True

        # Check it's followed
        rooms = tmp_db.get_followed_rooms("user", "u1")
        assert len(rooms) == 1
        assert rooms[0]["room_id"] == "!room:s"

        # Unfollow
        tmp_db.unfollow_room("user", "u1", "!room:s")
        rooms = tmp_db.get_followed_rooms("user", "u1")
        assert len(rooms) == 0

    def test_follow_idempotent(self, tmp_db):
        tmp_db.follow_room("user", "u1", "!room:s", "Room", "u1")
        tmp_db.follow_room("user", "u1", "!room:s", "Room", "u1")
        rooms = tmp_db.get_followed_rooms("user", "u1")
        assert len(rooms) == 1


class TestMatrixAccounts:
    def test_save_and_get_account(self, tmp_db):
        tmp_db.save_matrix_account(
            owner_type="user",
            owner_id="u1",
            homeserver_url="https://matrix.test",
            user_id="@alice:test",
            access_token="token123",
            device_id="DEVICE1",
            configured_by="u1",
        )
        account = tmp_db.get_matrix_account("user", "u1")
        assert account is not None
        assert account["user_id"] == "@alice:test"
        assert account["access_token"] == "token123"

    def test_update_account(self, tmp_db):
        tmp_db.save_matrix_account("user", "u1", "https://hs1", "@a:s", "tok1", "D1", "u1")
        tmp_db.save_matrix_account("user", "u1", "https://hs2", "@b:s", "tok2", "D2", "u1")
        account = tmp_db.get_matrix_account("user", "u1")
        assert account["homeserver_url"] == "https://hs2"

    def test_delete_account(self, tmp_db):
        tmp_db.save_matrix_account("user", "u1", "https://hs", "@a:s", "tok", "D1", "u1")
        tmp_db.follow_room("user", "u1", "!r:s", "Room", "u1")
        tmp_db.delete_matrix_account("user", "u1")
        assert tmp_db.get_matrix_account("user", "u1") is None
        assert len(tmp_db.get_followed_rooms("user", "u1")) == 0

    def test_get_all_accounts(self, tmp_db):
        tmp_db.save_matrix_account("user", "u1", "https://hs", "@a:s", "tok", "D1", "u1")
        tmp_db.save_matrix_account("group", "g1", "https://hs", "@b:s", "tok", "D1", "admin")
        accounts = tmp_db.get_all_matrix_accounts()
        assert len(accounts) == 2


class TestMultiTenantSync:
    def test_sync_state_per_owner(self, tmp_db):
        tmp_db.save_next_batch(ROOM_ID, "batch_global", "global", "global")
        tmp_db.save_next_batch(ROOM_ID, "batch_user", "user", "u1")

        assert tmp_db.get_next_batch(ROOM_ID, "global", "global") == "batch_global"
        assert tmp_db.get_next_batch(ROOM_ID, "user", "u1") == "batch_user"

    def test_messages_per_owner(self, tmp_db):
        now = int(time.time() * 1000)
        tmp_db.insert_message("$g1", ROOM_ID, "@u:s", now, "global msg", owner_type="global", owner_id="global")
        tmp_db.insert_message("$u1", ROOM_ID, "@u:s", now, "user msg", owner_type="user", owner_id="u1")

        global_msgs = tmp_db.get_messages(ROOM_ID, since_ts=now - 1000, owner_type="global", owner_id="global")
        user_msgs = tmp_db.get_messages(ROOM_ID, since_ts=now - 1000, owner_type="user", owner_id="u1")

        assert len(global_msgs) == 1
        assert global_msgs[0]["body"] == "global msg"
        assert len(user_msgs) == 1
        assert user_msgs[0]["body"] == "user msg"

    def test_message_count_per_owner(self, tmp_db):
        now = int(time.time() * 1000)
        tmp_db.insert_message("$a", ROOM_ID, "@u:s", now, "a", owner_type="global", owner_id="global")
        tmp_db.insert_message("$b", ROOM_ID, "@u:s", now, "b", owner_type="global", owner_id="global")
        tmp_db.insert_message("$c", ROOM_ID, "@u:s", now, "c", owner_type="user", owner_id="u1")

        assert tmp_db.get_message_count(ROOM_ID, "global", "global") == 2
        assert tmp_db.get_message_count(ROOM_ID, "user", "u1") == 1
        # Without owner filter, returns all
        assert tmp_db.get_message_count(ROOM_ID) == 3


class TestSSOSessions:
    def test_create_and_get_session(self, tmp_db):
        tmp_db.create_sso_session("state123", "user", "u1", "u1")
        session = tmp_db.get_sso_session("state123")
        assert session is not None
        assert session["owner_type"] == "user"
        assert session["completed"] == 0

    def test_complete_session(self, tmp_db):
        tmp_db.create_sso_session("state456", "user", "u2", "u2")
        tmp_db.complete_sso_session("state456", "access_tok", "@user:server")
        session = tmp_db.get_sso_session("state456")
        assert session["completed"] == 1
        assert session["access_token"] == "access_tok"

    def test_cleanup_old_sessions(self, tmp_db):
        tmp_db.create_sso_session("old_state", "user", "u1", "u1")
        # Manually set old timestamp
        with tmp_db._connect() as conn:
            conn.execute("UPDATE sso_sessions SET created_at = 1 WHERE state = 'old_state'")
        tmp_db.cleanup_sso_sessions(max_age_seconds=60)
        assert tmp_db.get_sso_session("old_state") is None
