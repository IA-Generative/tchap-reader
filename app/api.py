"""FastAPI route definitions."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.database import Database
from app.matrix_client import MatrixClient
from app.models import (
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
