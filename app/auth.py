"""Rights verification and OpenWebUI group membership checks."""

from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def get_user_groups(user_token: str) -> list[dict]:
    """Fetch groups the user belongs to from OpenWebUI API.

    Returns list of group dicts with at least 'id' and optionally 'admin_ids'.
    """
    url = f"{settings.OPENWEBUI_BASE_URL.rstrip('/')}/api/v1/groups/"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {user_token}"})
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.warning("Failed to fetch user groups from OpenWebUI: %s", exc)
        return []


async def check_access(user: dict, owner_type: str, owner_id: str) -> bool:
    """Check if a user has read access to an owner's resources.

    Rules:
    - admin: always allowed
    - owner_type 'user': only the user themselves
    - owner_type 'global': everyone
    - owner_type 'group': must be a member of the group
    """
    if user.get("role") == "admin":
        return True
    if owner_type == "user" and owner_id == user.get("id"):
        return True
    if owner_type == "global":
        return True
    if owner_type == "group":
        token = user.get("token", "")
        if not token:
            return False
        groups = await get_user_groups(token)
        return owner_id in [g["id"] for g in groups]
    return False


async def check_can_manage(user: dict, owner_type: str, owner_id: str) -> bool:
    """Check if a user can configure/manage an owner's resources.

    Rules:
    - admin: always allowed
    - owner_type 'user': only the user themselves
    - owner_type 'group': must be a group admin
    - owner_type 'global': admin only (handled above)
    """
    if user.get("role") == "admin":
        return True
    if owner_type == "user" and owner_id == user.get("id"):
        return True
    if owner_type == "group":
        token = user.get("token", "")
        if not token:
            return False
        groups = await get_user_groups(token)
        group = next((g for g in groups if g["id"] == owner_id), None)
        if group and user.get("id") in group.get("admin_ids", []):
            return True
    return False


async def get_accessible_owners(user: dict) -> list[dict]:
    """Get all owner refs the user has access to.

    Returns a list of {owner_type, owner_id} dicts.
    """
    owners = []

    # Personal access
    owners.append({"owner_type": "user", "owner_id": user["id"]})

    # Global access
    owners.append({"owner_type": "global", "owner_id": "global"})

    # Group access
    token = user.get("token", "")
    if token:
        groups = await get_user_groups(token)
        for group in groups:
            owners.append({"owner_type": "group", "owner_id": group["id"]})

    return owners
