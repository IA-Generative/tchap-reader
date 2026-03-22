"""FastAPI route definitions."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.database import Database
from app.matrix_client import MatrixClient
from app.models import (
    ConfigStatusResponse,
    ConfigureAccountRequest,
    ConfigureAccountResponse,
    DiscoverRoomsResponse,
    FollowRoomRequest,
    FollowRoomResponse,
    HealthResponse,
    MessagesRequest,
    MessagesResponse,
    MessageItem,
    RoomInfo,
    SummaryRequest,
    SummaryResponse,
    SenderStat,
    SyncRequest,
    SyncResponse,
)
from app.summary_service import SummaryService
from app.sync_service import SyncService

logger = logging.getLogger(__name__)

router = APIRouter()

# Shared instances
_db = Database()
_client = MatrixClient()
_sync = SyncService(_db, _client)
_summary = SummaryService(_db)

# Cache room names
_room_names: dict[str, str] = {}


async def _get_room_name(room_id: str) -> str:
    if room_id not in _room_names:
        _room_names[room_id] = await _client.get_room_name(room_id)
    return _room_names[room_id]


def _check_allowed(room_id: str) -> None:
    allowed = settings.allowed_rooms
    if allowed and room_id not in allowed:
        raise HTTPException(status_code=403, detail=f"Room {room_id} is not in the allowlist")


@router.get("/healthz", response_model=HealthResponse)
async def healthcheck() -> HealthResponse:
    missing = settings.validate_config()
    tracked = _db.get_tracked_rooms()
    total = _db.get_total_messages()
    return HealthResponse(
        status="healthy" if not missing else "unhealthy",
        rooms_tracked=len(tracked),
        total_messages=total,
        missing_config=missing,
    )


@router.get("/rooms", response_model=list[RoomInfo])
async def list_rooms() -> list[RoomInfo]:
    """List allowed rooms with sync stats."""
    allowed = settings.allowed_rooms
    if not allowed:
        return []

    result = []
    for room_id in allowed:
        name = await _get_room_name(room_id)
        count = _db.get_message_count(room_id)
        last_synced = _db.get_last_synced(room_id)
        from datetime import datetime, timezone
        ls = None
        if last_synced:
            ls = datetime.fromtimestamp(last_synced, tz=timezone.utc).isoformat()
        result.append(RoomInfo(
            room_id=room_id,
            name=name,
            message_count=count,
            last_synced=ls,
        ))
    return result


@router.post("/sync", response_model=SyncResponse)
async def sync_room(request: SyncRequest) -> SyncResponse:
    """Trigger incremental sync for a room."""
    _check_allowed(request.room_id)
    try:
        stats = await _sync.sync_room(request.room_id)
        return SyncResponse(**stats)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        logger.exception("Sync failed for %s: %s", request.room_id, exc)
        raise HTTPException(status_code=500, detail=f"Sync failed: {exc}")


@router.post("/messages", response_model=MessagesResponse)
async def get_messages(request: MessagesRequest) -> MessagesResponse:
    """Get stored messages for a room within a time window."""
    _check_allowed(request.room_id)

    import time as _time
    from datetime import datetime, timezone

    now_ms = int(_time.time() * 1000)
    since_ms = now_ms - (request.since_hours * 3600 * 1000)

    messages = _db.get_messages(
        room_id=request.room_id,
        since_ts=since_ms,
        until_ts=now_ms,
        limit=request.limit,
    )

    items = [
        MessageItem(
            event_id=m["event_id"],
            sender=m["sender"],
            timestamp=m["timestamp"],
            body=m["body"],
            reply_to=m.get("reply_to_event_id"),
            is_edit=bool(m.get("is_edit")),
        )
        for m in messages
    ]

    start = datetime.fromtimestamp(since_ms / 1000, tz=timezone.utc).isoformat()
    end = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat()

    return MessagesResponse(
        messages=items,
        total=len(items),
        window_start=start,
        window_end=end,
    )


# ── Admin endpoints ───────────────────────────────────────────

@router.get("/admin/status", response_model=ConfigStatusResponse)
async def admin_status() -> ConfigStatusResponse:
    """Get current configuration status."""
    _reload_config_from_db()
    configured = bool(settings.TCHAP_ACCESS_TOKEN and settings.TCHAP_USER_ID)
    rooms_detail = []
    for room_id in settings.allowed_rooms:
        name = await _get_room_name(room_id) if configured else room_id
        count = _db.get_message_count(room_id)
        last_synced = _db.get_last_synced(room_id)
        from datetime import datetime, timezone
        ls = datetime.fromtimestamp(last_synced, tz=timezone.utc).isoformat() if last_synced else None
        rooms_detail.append(RoomInfo(room_id=room_id, name=name, message_count=count, last_synced=ls))

    return ConfigStatusResponse(
        configured=configured,
        homeserver_url=settings.TCHAP_HOMESERVER_URL,
        user_id=settings.TCHAP_USER_ID,
        allowed_rooms=sorted(settings.allowed_rooms),
        total_messages=_db.get_total_messages(),
        rooms_detail=rooms_detail,
    )


@router.post("/admin/configure", response_model=ConfigureAccountResponse)
async def admin_configure(request: ConfigureAccountRequest) -> ConfigureAccountResponse:
    """Configure the Tchap bot account. Tests the connection before saving."""
    import httpx

    # Test the connection first
    test_url = f"{request.homeserver_url.rstrip('/')}/_matrix/client/v3/joined_rooms"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(test_url, headers={"Authorization": f"Bearer {request.access_token}"})
        if resp.status_code == 401:
            return ConfigureAccountResponse(ok=False, message="Authentification échouée (401). Vérifiez le token.")
        resp.raise_for_status()
        joined_rooms = resp.json().get("joined_rooms", [])
    except Exception as exc:
        return ConfigureAccountResponse(ok=False, message=f"Connexion échouée : {exc}")

    # Save to DB for persistence
    _db.set_config("homeserver_url", request.homeserver_url)
    _db.set_config("user_id", request.user_id)
    _db.set_config("access_token", request.access_token)
    _db.set_config("device_id", request.device_id)

    # Update runtime settings
    settings.TCHAP_HOMESERVER_URL = request.homeserver_url
    settings.TCHAP_USER_ID = request.user_id
    settings.TCHAP_ACCESS_TOKEN = request.access_token
    settings.TCHAP_DEVICE_ID = request.device_id

    # Reinitialize the client
    _reinit_client()

    return ConfigureAccountResponse(
        ok=True,
        message=f"Compte configuré. {len(joined_rooms)} salon(s) rejoint(s).",
        user_id=request.user_id,
        joined_rooms=joined_rooms,
    )


@router.get("/admin/discover-rooms", response_model=DiscoverRoomsResponse)
async def admin_discover_rooms() -> DiscoverRoomsResponse:
    """List all rooms the bot has joined on the homeserver."""
    if not settings.TCHAP_ACCESS_TOKEN:
        return DiscoverRoomsResponse(ok=False, message="Compte non configuré. Utilisez /admin/configure d'abord.")

    try:
        room_ids = await _client.get_joined_rooms()
    except Exception as exc:
        return DiscoverRoomsResponse(ok=False, message=f"Erreur : {exc}")

    rooms = []
    for room_id in room_ids:
        name = await _client.get_room_name(room_id)
        is_followed = room_id in settings.allowed_rooms
        rooms.append({"room_id": room_id, "name": name, "followed": is_followed})

    return DiscoverRoomsResponse(ok=True, rooms=rooms)


@router.post("/admin/follow-room", response_model=FollowRoomResponse)
async def admin_follow_room(request: FollowRoomRequest) -> FollowRoomResponse:
    """Add a room to the followed list."""
    current = settings.allowed_rooms
    if request.room_id in current:
        return FollowRoomResponse(ok=True, message="Salon déjà suivi.", allowed_rooms=sorted(current))

    current.add(request.room_id)
    new_list = ",".join(sorted(current))
    settings.TCHAP_ALLOWED_ROOM_IDS = new_list
    _db.set_config("allowed_room_ids", new_list)

    if request.name:
        _room_names[request.room_id] = request.name

    return FollowRoomResponse(ok=True, message=f"Salon {request.room_id} ajouté.", allowed_rooms=sorted(current))


@router.post("/admin/unfollow-room", response_model=FollowRoomResponse)
async def admin_unfollow_room(request: FollowRoomRequest) -> FollowRoomResponse:
    """Remove a room from the followed list."""
    current = settings.allowed_rooms
    current.discard(request.room_id)
    new_list = ",".join(sorted(current))
    settings.TCHAP_ALLOWED_ROOM_IDS = new_list
    _db.set_config("allowed_room_ids", new_list)

    return FollowRoomResponse(ok=True, message=f"Salon {request.room_id} retiré.", allowed_rooms=sorted(current))


def _reload_config_from_db() -> None:
    """Reload config from DB on startup (DB takes priority over env vars if set)."""
    stored = _db.get_all_config()
    if stored.get("homeserver_url"):
        settings.TCHAP_HOMESERVER_URL = stored["homeserver_url"]
    if stored.get("access_token"):
        settings.TCHAP_ACCESS_TOKEN = stored["access_token"]
    if stored.get("user_id"):
        settings.TCHAP_USER_ID = stored["user_id"]
    if stored.get("device_id"):
        settings.TCHAP_DEVICE_ID = stored["device_id"]
    if stored.get("allowed_room_ids"):
        settings.TCHAP_ALLOWED_ROOM_IDS = stored["allowed_room_ids"]


def _reinit_client() -> None:
    """Reinitialize the Matrix client after config change."""
    global _client, _sync
    _client = MatrixClient()
    _sync = SyncService(_db, _client)
    _room_names.clear()


# Load persisted config on module import
_reload_config_from_db()


@router.post("/summary", response_model=SummaryResponse)
async def get_summary(request: SummaryRequest) -> SummaryResponse:
    """Get a compact summary prepared for LLM analysis."""
    _check_allowed(request.room_id)
    room_name = await _get_room_name(request.room_id)

    data = _summary.get_summary(
        room_id=request.room_id,
        room_name=room_name,
        since_hours=request.since_hours,
        max_messages=request.max_messages,
    )

    return SummaryResponse(
        room_id=data["room_id"],
        room_name=data["room_name"],
        period=data["period"],
        message_count=data["message_count"],
        unique_senders=data["unique_senders"],
        top_senders=[SenderStat(**s) for s in data["top_senders"]],
        messages_for_llm=data["messages_for_llm"],
    )
