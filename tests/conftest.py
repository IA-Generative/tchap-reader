"""Shared test fixtures."""

import os
import tempfile

import pytest

# Set test env before importing app
_tmp = tempfile.mkdtemp()
os.environ["TCHAP_ACCESS_TOKEN"] = "test_token"
os.environ["TCHAP_USER_ID"] = "@testbot:agent.tchap.gouv.fr"
os.environ["TCHAP_ALLOWED_ROOM_IDS"] = "!testroom:agent.tchap.gouv.fr"
os.environ["TCHAP_HOMESERVER_URL"] = "https://matrix.test.local"
os.environ["TCHAP_STORE_PATH"] = os.path.join(_tmp, "test_default.db")
os.environ["OPENWEBUI_BASE_URL"] = "http://localhost:9999"
os.environ["SSO_CALLBACK_BASE_URL"] = "http://localhost:8087"


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary SQLite database."""
    db_path = str(tmp_path / "test.db")
    os.environ["TCHAP_STORE_PATH"] = db_path
    from app.database import Database
    return Database(db_path)


@pytest.fixture
def test_client(tmp_path):
    """FastAPI test client with temporary database."""
    db_path = str(tmp_path / "api_test.db")
    os.environ["TCHAP_STORE_PATH"] = db_path

    # Force reimport to pick up new db path
    import importlib
    import app.api
    importlib.reload(app.api)

    from app.main import create_app
    from fastapi.testclient import TestClient

    test_app = create_app()
    with TestClient(test_app) as client:
        yield client
