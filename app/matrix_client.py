"""HTTP client for Matrix/Tchap API — multi-account support."""

from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import quote

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class MatrixClient:
    """Lightweight Matrix client for read-only sync operations.

    Supports multi-account by accepting per-call overrides for base_url and token.
    Falls back to global settings if not provided.
    """

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
    ):
        self._base_url = (base_url or settings.TCHAP_HOMESERVER_URL).rstrip("/")
        self._token = token or settings.TCHAP_ACCESS_TOKEN
        self._rate_limit = settings.TCHAP_API_RATE_LIMIT_PER_SEC
        self._last_request_time: float = 0

    def _headers(self, token: str | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer {token or self._token}"}

    async def _rate_limit_wait(self) -> None:
        """Enforce rate limiting between requests."""
        if self._rate_limit <= 0:
            return
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_request_time
        min_interval = 1.0 / self._rate_limit
        if elapsed < min_interval:
            await asyncio.sleep(min_interval - elapsed)
        self._last_request_time = asyncio.get_event_loop().time()

    async def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json_body: dict | None = None,
        timeout: int = 30,
        retries: int = 2,
        base_url: str | None = None,
        token: str | None = None,
    ) -> dict:
        """Make an authenticated request to the Matrix API with retry and rate limiting."""
        url = f"{(base_url or self._base_url).rstrip('/')}{path}"

        for attempt in range(retries + 1):
            await self._rate_limit_wait()

            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.request(
                        method, url,
                        headers=self._headers(token),
                        params=params,
                        json=json_body,
                    )

                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "5"))
                    logger.warning("Rate limited by Matrix server, waiting %ds", retry_after)
                    await asyncio.sleep(retry_after)
                    continue

                if resp.status_code == 401:
                    logger.error("Authentication failed — check access token")
                    raise PermissionError("Matrix authentication failed (401)")

                resp.raise_for_status()
                return resp.json()

            except httpx.TimeoutException:
                if attempt < retries:
                    wait = 2 ** (attempt + 1)
                    logger.warning("Timeout on %s, retrying in %ds (attempt %d/%d)",
                                   path, wait, attempt + 1, retries)
                    await asyncio.sleep(wait)
                else:
                    raise
            except httpx.HTTPStatusError:
                raise

        raise RuntimeError(f"Max retries exceeded for {path}")

    async def get_joined_rooms(self) -> list[str]:
        """Get list of joined room IDs."""
        data = await self._request("GET", "/_matrix/client/v3/joined_rooms")
        return data.get("joined_rooms", [])

    async def get_room_name(self, room_id: str) -> str:
        """Get the display name of a room."""
        try:
            encoded = quote(room_id, safe="")
            data = await self._request(
                "GET", f"/_matrix/client/v3/rooms/{encoded}/state/m.room.name"
            )
            return data.get("name", room_id)
        except Exception:
            return room_id

    async def sync(
        self,
        since: str | None = None,
        room_ids: list[str] | None = None,
        timeout_ms: int = 0,
    ) -> dict:
        """Perform an incremental sync.

        Returns the raw sync response with timeline events.
        """
        sync_filter = {
            "room": {
                "timeline": {
                    "types": ["m.room.message", "m.room.redaction"],
                    "limit": 100,
                },
                "state": {"types": []},
                "ephemeral": {"types": []},
                "account_data": {"types": []},
            },
            "presence": {"types": []},
            "account_data": {"types": []},
        }

        if room_ids:
            sync_filter["room"]["rooms"] = room_ids

        params: dict[str, str] = {
            "filter": json.dumps(sync_filter),
            "timeout": str(timeout_ms),
        }
        if since:
            params["since"] = since

        return await self._request(
            "GET", "/_matrix/client/v3/sync", params=params, timeout=60
        )


def create_client_for_account(account: dict) -> MatrixClient:
    """Create a MatrixClient instance for a specific account from the DB."""
    return MatrixClient(
        base_url=account["homeserver_url"],
        token=account["access_token"],
    )
