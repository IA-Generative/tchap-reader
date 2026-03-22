"""Tests for auth module — rights verification."""

import pytest
from unittest.mock import AsyncMock, patch

from app.auth import check_access, check_can_manage, get_accessible_owners


ADMIN_USER = {"id": "admin-uuid", "role": "admin", "token": "jwt_admin"}
REGULAR_USER = {"id": "user-uuid", "role": "user", "token": "jwt_user"}
NO_TOKEN_USER = {"id": "user-uuid", "role": "user", "token": ""}


class TestCheckAccess:
    @pytest.mark.asyncio
    async def test_admin_always_allowed(self):
        assert await check_access(ADMIN_USER, "user", "other-uuid") is True
        assert await check_access(ADMIN_USER, "group", "group-uuid") is True
        assert await check_access(ADMIN_USER, "global", "global") is True

    @pytest.mark.asyncio
    async def test_user_accesses_own(self):
        assert await check_access(REGULAR_USER, "user", "user-uuid") is True

    @pytest.mark.asyncio
    async def test_user_cannot_access_other(self):
        assert await check_access(REGULAR_USER, "user", "other-uuid") is False

    @pytest.mark.asyncio
    async def test_global_always_readable(self):
        assert await check_access(REGULAR_USER, "global", "global") is True

    @pytest.mark.asyncio
    @patch("app.auth.get_user_groups")
    async def test_user_in_group(self, mock_groups):
        mock_groups.return_value = [{"id": "group-1"}, {"id": "group-2"}]
        assert await check_access(REGULAR_USER, "group", "group-1") is True

    @pytest.mark.asyncio
    @patch("app.auth.get_user_groups")
    async def test_user_not_in_group(self, mock_groups):
        mock_groups.return_value = [{"id": "group-1"}]
        assert await check_access(REGULAR_USER, "group", "group-3") is False

    @pytest.mark.asyncio
    async def test_no_token_group_denied(self):
        assert await check_access(NO_TOKEN_USER, "group", "group-1") is False


class TestCheckCanManage:
    @pytest.mark.asyncio
    async def test_admin_can_manage_all(self):
        assert await check_can_manage(ADMIN_USER, "global", "global") is True

    @pytest.mark.asyncio
    async def test_user_can_manage_own(self):
        assert await check_can_manage(REGULAR_USER, "user", "user-uuid") is True

    @pytest.mark.asyncio
    async def test_user_cannot_manage_global(self):
        assert await check_can_manage(REGULAR_USER, "global", "global") is False

    @pytest.mark.asyncio
    @patch("app.auth.get_user_groups")
    async def test_group_admin_can_manage(self, mock_groups):
        mock_groups.return_value = [
            {"id": "group-1", "admin_ids": ["user-uuid"]},
        ]
        assert await check_can_manage(REGULAR_USER, "group", "group-1") is True

    @pytest.mark.asyncio
    @patch("app.auth.get_user_groups")
    async def test_group_member_cannot_manage(self, mock_groups):
        mock_groups.return_value = [
            {"id": "group-1", "admin_ids": ["other-uuid"]},
        ]
        assert await check_can_manage(REGULAR_USER, "group", "group-1") is False


class TestGetAccessibleOwners:
    @pytest.mark.asyncio
    @patch("app.auth.get_user_groups")
    async def test_returns_personal_global_groups(self, mock_groups):
        mock_groups.return_value = [{"id": "g1"}, {"id": "g2"}]
        owners = await get_accessible_owners(REGULAR_USER)
        owner_types = {(o["owner_type"], o["owner_id"]) for o in owners}
        assert ("user", "user-uuid") in owner_types
        assert ("global", "global") in owner_types
        assert ("group", "g1") in owner_types
        assert ("group", "g2") in owner_types
