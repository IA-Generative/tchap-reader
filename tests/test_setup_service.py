"""Tests for setup service — login flows."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.setup_service import SetupService


class TestSetupService:
    @pytest.fixture
    def service(self, tmp_db):
        return SetupService(tmp_db), tmp_db

    @pytest.mark.asyncio
    @patch("app.setup_service.httpx.AsyncClient")
    async def test_login_token_success(self, mock_client_class, service):
        svc, db = service
        # Mock whoami response
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "user_id": "@alice:agent.tchap.gouv.fr",
            "device_id": "DEVICE1",
        }

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = await svc.login_token(
            token="syt_valid_token",
            owner_type="user",
            owner_id="user-123",
            user_uuid="user-123",
        )

        assert result["ok"] is True
        assert result["user_id"] == "@alice:agent.tchap.gouv.fr"

        # Check account saved
        account = db.get_matrix_account("user", "user-123")
        assert account is not None
        assert account["access_token"] == "syt_valid_token"

    @pytest.mark.asyncio
    @patch("app.setup_service.httpx.AsyncClient")
    async def test_login_token_invalid(self, mock_client_class, service):
        svc, db = service
        mock_resp = MagicMock()
        mock_resp.status_code = 401

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = await svc.login_token(
            token="invalid",
            owner_type="user",
            owner_id="user-123",
            user_uuid="user-123",
        )

        assert result["ok"] is False
        assert db.get_matrix_account("user", "user-123") is None

    @pytest.mark.asyncio
    @patch("app.setup_service.httpx.AsyncClient")
    async def test_login_password_success(self, mock_client_class, service):
        svc, db = service
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "access_token": "syt_new_token",
            "user_id": "@bob:agent.tchap.gouv.fr",
            "device_id": "D2",
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = await svc.login_password(
            email="bob@interieur.gouv.fr",
            password="secret",
            owner_type="user",
            owner_id="user-456",
            user_uuid="user-456",
        )

        assert result["ok"] is True
        assert result["user_id"] == "@bob:agent.tchap.gouv.fr"

        account = db.get_matrix_account("user", "user-456")
        assert account is not None
        assert account["access_token"] == "syt_new_token"

    @pytest.mark.asyncio
    @patch("app.setup_service.httpx.AsyncClient")
    async def test_sso_start_available(self, mock_client_class, service):
        svc, db = service
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "flows": [
                {"type": "m.login.sso"},
                {"type": "m.login.password"},
            ]
        }

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = await svc.start_sso("user", "user-789", "user-789")

        assert result["ok"] is True
        assert "url" in result
        assert result["state"]

        # SSO session should be stored
        session = db.get_sso_session(result["state"])
        assert session is not None
        assert session["owner_type"] == "user"

    @pytest.mark.asyncio
    @patch("app.setup_service.httpx.AsyncClient")
    async def test_sso_start_not_available(self, mock_client_class, service):
        svc, db = service
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "flows": [{"type": "m.login.password"}]
        }

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = await svc.start_sso("user", "user-789", "user-789")
        assert result["ok"] is False
