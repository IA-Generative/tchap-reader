"""Tests for SQLite database repository."""

import time
import pytest
from tests.mock_data import ROOM_ID


class TestMessageCRUD:
    def test_insert_message(self, tmp_db):
        ok = tmp_db.insert_message("$e1", ROOM_ID, "@u:s", int(time.time() * 1000), "hello")
        assert ok is True

    def test_duplicate_ignored(self, tmp_db):
        tmp_db.insert_message("$e1", ROOM_ID, "@u:s", int(time.time() * 1000), "hello")
        ok = tmp_db.insert_message("$e1", ROOM_ID, "@u:s", int(time.time() * 1000), "hello")
        assert ok is False

    def test_get_messages_window(self, tmp_db):
        now = int(time.time() * 1000)
        tmp_db.insert_message("$old", ROOM_ID, "@u:s", now - 999_999_999, "old")
        tmp_db.insert_message("$new", ROOM_ID, "@u:s", now - 1000, "new")
        msgs = tmp_db.get_messages(ROOM_ID, since_ts=now - 3600_000)
        assert len(msgs) == 1
        assert msgs[0]["event_id"] == "$new"

    def test_redaction(self, tmp_db):
        now = int(time.time() * 1000)
        tmp_db.insert_message("$r1", ROOM_ID, "@u:s", now, "to be redacted")
        tmp_db.mark_redacted("$r1")
        msgs = tmp_db.get_messages(ROOM_ID, since_ts=now - 1000)
        assert len(msgs) == 0  # redacted messages excluded

    def test_edit(self, tmp_db):
        now = int(time.time() * 1000)
        tmp_db.insert_message("$orig", ROOM_ID, "@u:s", now, "original text")
        tmp_db.apply_edit("$orig", "edited text", "$edit1")
        msgs = tmp_db.get_messages(ROOM_ID, since_ts=now - 1000)
        assert msgs[0]["body"] == "edited text"

    def test_message_count(self, tmp_db):
        now = int(time.time() * 1000)
        tmp_db.insert_message("$c1", ROOM_ID, "@u:s", now, "one")
        tmp_db.insert_message("$c2", ROOM_ID, "@u:s", now, "two")
        assert tmp_db.get_message_count(ROOM_ID) == 2


class TestSyncState:
    def test_save_and_get_batch(self, tmp_db):
        tmp_db.save_next_batch(ROOM_ID, "s123")
        assert tmp_db.get_next_batch(ROOM_ID) == "s123"

    def test_no_batch_returns_none(self, tmp_db):
        assert tmp_db.get_next_batch("!unknown:s") is None

    def test_update_batch(self, tmp_db):
        tmp_db.save_next_batch(ROOM_ID, "s1")
        tmp_db.save_next_batch(ROOM_ID, "s2")
        assert tmp_db.get_next_batch(ROOM_ID) == "s2"
