"""Tests for summary service — pseudonymization, formatting."""

import time
import pytest

from app.summary_service import SummaryService
from tests.mock_data import ROOM_ID, ROOM_NAME


class TestSummaryService:
    @pytest.fixture
    def service(self, tmp_db):
        # Insert test messages
        now = int(time.time() * 1000)
        for i in range(5):
            tmp_db.insert_message(
                f"$msg{i}", ROOM_ID,
                f"@user{i % 3}:agent.tchap.gouv.fr",
                now - (i * 60_000),
                f"Message numéro {i} avec du contenu de test",
            )
        return SummaryService(tmp_db)

    def test_summary_has_messages(self, service):
        result = service.get_summary(ROOM_ID, ROOM_NAME, since_hours=1)
        assert result["message_count"] == 5
        assert len(result["messages_for_llm"]) > 0

    def test_pseudonymization(self, service):
        result = service.get_summary(ROOM_ID, ROOM_NAME, since_hours=1)
        text = result["messages_for_llm"]
        # Real user IDs should NOT appear
        assert "@user0:agent.tchap.gouv.fr" not in text
        assert "@user1:agent.tchap.gouv.fr" not in text
        # Pseudonyms should appear
        assert "Utilisateur_" in text

    def test_top_senders(self, service):
        result = service.get_summary(ROOM_ID, ROOM_NAME, since_hours=1)
        assert len(result["top_senders"]) > 0
        for s in result["top_senders"]:
            assert s["pseudonym"].startswith("Utilisateur_")

    def test_empty_window(self, tmp_db):
        svc = SummaryService(tmp_db)
        result = svc.get_summary("!empty:s", "Empty", since_hours=1)
        assert result["message_count"] == 0

    def test_truncation(self, tmp_db):
        # Insert many long messages
        now = int(time.time() * 1000)
        for i in range(100):
            tmp_db.insert_message(
                f"$long{i}", ROOM_ID,
                "@user:s", now - (i * 1000),
                "x" * 500,
            )
        svc = SummaryService(tmp_db)
        result = svc.get_summary(ROOM_ID, ROOM_NAME, since_hours=1, max_messages=100)
        # Should be truncated to ~12000 chars
        assert len(result["messages_for_llm"]) <= 13000
