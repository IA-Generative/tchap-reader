"""Shared test fixtures."""

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

# Set test env before importing app
os.environ["TCHAP_ACCESS_TOKEN"] = "test_token"
os.environ["TCHAP_USER_ID"] = "@testbot:agent.tchap.gouv.fr"
os.environ["TCHAP_ALLOWED_ROOM_IDS"] = "!testroom:agent.tchap.gouv.fr"
os.environ["TCHAP_HOMESERVER_URL"] = "https://matrix.test.local"


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary SQLite database."""
    db_path = str(tmp_path / "test.db")
    os.environ["TCHAP_STORE_PATH"] = db_path
    from app.database import Database
    return Database(db_path)


@pytest.fixture
def test_client(tmp_db):
    """FastAPI test client with temporary database."""
    from app.main import app
    with TestClient(app) as client:
        yield client
