"""Pydantic models for the API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SyncRequest(BaseModel):
    room_id: str


class SyncResponse(BaseModel):
    room_id: str
    synced: int
    new_messages: int
    next_batch: str | None = None


class MessagesRequest(BaseModel):
    room_id: str
    since_hours: int = 168
    limit: int = 1000


class MessageItem(BaseModel):
    event_id: str
    sender: str
    timestamp: int
    body: str
    reply_to: str | None = None
    is_edit: bool = False


class MessagesResponse(BaseModel):
    messages: list[MessageItem]
    total: int
    window_start: str
    window_end: str


class SummaryRequest(BaseModel):
    room_id: str
    since_hours: int = 168
    max_messages: int = 500


class SenderStat(BaseModel):
    pseudonym: str
    message_count: int


class SummaryResponse(BaseModel):
    room_id: str
    room_name: str
    period: str
    message_count: int
    unique_senders: int
    top_senders: list[SenderStat]
    messages_for_llm: str


class RoomInfo(BaseModel):
    room_id: str
    name: str
    message_count: int
    last_synced: str | None = None


class HealthResponse(BaseModel):
    status: str
    rooms_tracked: int
    total_messages: int
    missing_config: list[str] = Field(default_factory=list)


# --- Admin models ---

class ConfigureAccountRequest(BaseModel):
    homeserver_url: str = Field(description="URL du homeserver (ex: https://matrix.agent.tchap.gouv.fr)")
    user_id: str = Field(description="Matrix user ID du bot (ex: @bot:agent.tchap.gouv.fr)")
    access_token: str = Field(description="Access token du compte bot")
    device_id: str = Field(default="OWUI_BOT", description="Device ID (optionnel)")


class ConfigureAccountResponse(BaseModel):
    ok: bool
    message: str
    user_id: str = ""
    joined_rooms: list[str] = Field(default_factory=list)


class DiscoverRoomsResponse(BaseModel):
    ok: bool
    rooms: list[dict] = Field(default_factory=list)
    message: str = ""


class FollowRoomRequest(BaseModel):
    room_id: str
    name: str = ""


class FollowRoomResponse(BaseModel):
    ok: bool
    message: str
    allowed_rooms: list[str] = Field(default_factory=list)


class ConfigStatusResponse(BaseModel):
    configured: bool
    homeserver_url: str
    user_id: str
    allowed_rooms: list[str]
    total_messages: int
    rooms_detail: list[RoomInfo] = Field(default_factory=list)
