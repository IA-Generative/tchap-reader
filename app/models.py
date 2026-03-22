"""Pydantic models for the API — multi-tenant version."""

from __future__ import annotations

from pydantic import BaseModel, Field


# --- Owner types ---

OWNER_USER = "user"
OWNER_GROUP = "group"
OWNER_GLOBAL = "global"
VALID_OWNER_TYPES = {OWNER_USER, OWNER_GROUP, OWNER_GLOBAL}


# --- Multi-tenant base ---

class OwnerRef(BaseModel):
    owner_type: str = Field(description="Type d'owner : user, group, global")
    owner_id: str = Field(description="UUID utilisateur, UUID groupe, ou 'global'")


# --- Sync & messages ---

class SyncRequest(BaseModel):
    room_id: str
    owner_type: str = OWNER_GLOBAL
    owner_id: str = "global"


class SyncResponse(BaseModel):
    room_id: str
    synced: int
    new_messages: int
    next_batch: str | None = None


class MessagesRequest(BaseModel):
    room_id: str
    since_hours: int = 168
    limit: int = 1000
    owner_type: str = OWNER_GLOBAL
    owner_id: str = "global"


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
    owner_type: str = OWNER_GLOBAL
    owner_id: str = "global"


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
    owner_type: str = OWNER_GLOBAL
    owner_id: str = "global"


class HealthResponse(BaseModel):
    status: str
    rooms_tracked: int
    total_messages: int
    missing_config: list[str] = Field(default_factory=list)


# --- Setup models ---

class SetupStartResponse(BaseModel):
    ok: bool
    message: str
    options: list[str] = Field(default_factory=list)


class SSOStartRequest(BaseModel):
    owner_type: str = OWNER_USER
    owner_id: str = ""


class SSOStartResponse(BaseModel):
    ok: bool
    url: str = ""
    state: str = ""
    message: str = ""


class SSOCompleteRequest(BaseModel):
    state: str


class SSOCompleteResponse(BaseModel):
    ok: bool
    message: str
    user_id: str = ""


class LoginPasswordRequest(BaseModel):
    email: str
    password: str
    owner_type: str = OWNER_USER
    owner_id: str = ""


class LoginTokenRequest(BaseModel):
    token: str
    owner_type: str = OWNER_USER
    owner_id: str = ""


class LoginResponse(BaseModel):
    ok: bool
    message: str
    user_id: str = ""


# --- Room management ---

class FollowRoomRequest(BaseModel):
    room_id: str
    name: str = ""
    owner_type: str = OWNER_GLOBAL
    owner_id: str = "global"


class FollowRoomResponse(BaseModel):
    ok: bool
    message: str
    followed_rooms: list[str] = Field(default_factory=list)


class DiscoverRoomsRequest(BaseModel):
    owner_type: str = OWNER_USER
    owner_id: str = ""


class DiscoverRoomsResponse(BaseModel):
    ok: bool
    rooms: list[dict] = Field(default_factory=list)
    message: str = ""


# --- Admin models ---

class ConfigStatusResponse(BaseModel):
    configured: bool
    homeserver_url: str
    user_id: str
    allowed_rooms: list[str]
    total_messages: int
    rooms_detail: list[RoomInfo] = Field(default_factory=list)


class AllAccessEntry(BaseModel):
    owner_type: str
    owner_id: str
    homeserver_url: str
    user_id: str
    configured_by: str
    created_at: int


class AllAccessResponse(BaseModel):
    ok: bool
    entries: list[AllAccessEntry] = Field(default_factory=list)


class SetGlobalRequest(BaseModel):
    room_id: str


class RevokeRequest(BaseModel):
    owner_type: str
    owner_id: str


class AdminActionResponse(BaseModel):
    ok: bool
    message: str


# --- User context (from OpenWebUI __user__) ---

class UserContext(BaseModel):
    id: str
    email: str = ""
    name: str = ""
    role: str = "user"
    token: str = ""
