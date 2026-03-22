"""FastAPI route definitions — multi-tenant version."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request

from app.auth import check_access, check_can_manage, get_accessible_owners
from app.config import settings
from app.database import Database
from app.matrix_client import MatrixClient, create_client_for_account
from app.models import (
    AdminActionResponse,
    AllAccessEntry,
    AllAccessResponse,
    DiscoverRoomsResponse,
    FollowRoomRequest,
    FollowRoomResponse,
    HealthResponse,
    LoginPasswordRequest,
    LoginResponse,
    LoginTokenRequest,
    MessageItem,
    MessagesRequest,
    MessagesResponse,
    RevokeRequest,
    RoomInfo,
    SSOCompleteRequest,
    SSOCompleteResponse,
    SSOStartRequest,
    SSOStartResponse,
    SetGlobalRequest,
    SummaryRequest,
    SummaryResponse,
    SenderStat,
    SyncRequest,
    SyncResponse,
    UserContext,
)
from app.setup_service import SetupService
from app.summary_service import SummaryService
from app.sync_service import SyncService

logger = logging.getLogger(__name__)

router = APIRouter()

# Shared instances
_db = Database()
_default_client = MatrixClient()
_setup = SetupService(_db)
_summary = SummaryService(_db)

# Cache room names per (owner_type, owner_id)
_room_names: dict[str, str] = {}


def _extract_user(request: Request) -> dict:
    """Extract user context from request headers or body.

    In production, OpenWebUI tool calls pass user info via the backend.
    For API calls, we accept it via headers or query params.
    """
    user_id = request.headers.get("X-User-Id", "")
    user_role = request.headers.get("X-User-Role", "user")
    user_email = request.headers.get("X-User-Email", "")
    user_token = request.headers.get("X-User-Token", "")
    return {
        "id": user_id,
        "email": user_email,
        "role": user_role,
        "token": user_token,
    }


def _get_client_for_owner(owner_type: str, owner_id: str) -> MatrixClient:
    """Get or create a Matrix client for the given owner."""
    account = _db.get_matrix_account(owner_type, owner_id)
    if account:
        return create_client_for_account(account)
    # Fallback to default client (legacy behavior)
    return _default_client


async def _get_room_name(room_id: str, client: MatrixClient | None = None) -> str:
    """Get room name with caching."""
    if room_id not in _room_names:
        c = client or _default_client
        _room_names[room_id] = await c.get_room_name(room_id)
    return _room_names[room_id]


def _ts_to_iso(ts: int | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


# ── Health ───────────────────────────────────────────────────

@router.get("/healthz", response_model=HealthResponse)
async def healthcheck() -> HealthResponse:
    tracked = _db.get_tracked_rooms()
    total = _db.get_total_messages()
    accounts = _db.get_all_matrix_accounts()
    missing = settings.validate_config() if not accounts else []
    return HealthResponse(
        status="healthy" if (not missing or accounts) else "unhealthy",
        rooms_tracked=len(tracked),
        total_messages=total,
        missing_config=missing,
    )


# ── Rooms (multi-tenant) ────────────────────────────────────

@router.get("/rooms", response_model=list[RoomInfo])
async def list_rooms(
    request: Request,
    user_id: str = Query("", alias="user_id"),
) -> list[RoomInfo]:
    """List rooms accessible to the user (personal + groups + global)."""
    user = _extract_user(request)
    if not user["id"] and user_id:
        user["id"] = user_id

    if not user["id"]:
        # Legacy behavior: return rooms from allowed_rooms setting
        return await _legacy_list_rooms()

    # Get all accessible owners
    owners = await get_accessible_owners(user)

    result = []
    for owner in owners:
        ot, oid = owner["owner_type"], owner["owner_id"]
        account = _db.get_matrix_account(ot, oid)
        if not account:
            continue

        client = create_client_for_account(account)
        followed = _db.get_followed_rooms(ot, oid)
        for room in followed:
            name = await _get_room_name(room["room_id"], client)
            count = _db.get_message_count(room["room_id"], ot, oid)
            last_synced = _db.get_last_synced(room["room_id"], ot, oid)
            result.append(RoomInfo(
                room_id=room["room_id"],
                name=name if name != room["room_id"] else (room.get("room_name") or room["room_id"]),
                message_count=count,
                last_synced=_ts_to_iso(last_synced),
                owner_type=ot,
                owner_id=oid,
            ))

    return result


async def _legacy_list_rooms() -> list[RoomInfo]:
    """Legacy room listing from env-configured allowed_rooms."""
    allowed = settings.allowed_rooms
    if not allowed:
        return []
    result = []
    for room_id in allowed:
        name = await _get_room_name(room_id)
        count = _db.get_message_count(room_id)
        last_synced = _db.get_last_synced(room_id)
        result.append(RoomInfo(
            room_id=room_id,
            name=name,
            message_count=count,
            last_synced=_ts_to_iso(last_synced),
        ))
    return result


# ── Sync (multi-tenant) ─────────────────────────────────────

@router.post("/sync", response_model=SyncResponse)
async def sync_room(body: SyncRequest, request: Request) -> SyncResponse:
    """Trigger incremental sync for a room."""
    user = _extract_user(request)

    # Determine which account to use
    ot, oid = body.owner_type, body.owner_id

    # Access check
    if user["id"]:
        if not await check_access(user, ot, oid):
            raise HTTPException(status_code=403, detail="Accès refusé à ce owner")

    # Get the right client
    client = _get_client_for_owner(ot, oid)
    sync_svc = SyncService(_db, client)

    # Get allowed rooms for this owner
    followed = _db.get_all_followed_room_ids(ot, oid)
    # Also allow legacy allowed_rooms
    allowed = followed | settings.allowed_rooms if not followed else followed

    try:
        stats = await sync_svc.sync_room(
            body.room_id,
            owner_type=ot,
            owner_id=oid,
            allowed_rooms=allowed if allowed else None,
        )
        return SyncResponse(**stats)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        logger.exception("Sync failed for %s: %s", body.room_id, exc)
        raise HTTPException(status_code=500, detail=f"Sync failed: {exc}")


# ── Messages (multi-tenant) ─────────────────────────────────

@router.post("/messages", response_model=MessagesResponse)
async def get_messages(body: MessagesRequest, request: Request) -> MessagesResponse:
    """Get stored messages for a room within a time window."""
    user = _extract_user(request)
    ot, oid = body.owner_type, body.owner_id

    if user["id"]:
        if not await check_access(user, ot, oid):
            raise HTTPException(status_code=403, detail="Accès refusé")

    import time as _time
    now_ms = int(_time.time() * 1000)
    since_ms = now_ms - (body.since_hours * 3600 * 1000)

    messages = _db.get_messages(
        room_id=body.room_id,
        since_ts=since_ms,
        until_ts=now_ms,
        limit=body.limit,
        owner_type=ot,
        owner_id=oid,
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

    return MessagesResponse(messages=items, total=len(items), window_start=start, window_end=end)


# ── Summary (multi-tenant) ──────────────────────────────────

@router.post("/summary", response_model=SummaryResponse)
async def get_summary(body: SummaryRequest, request: Request) -> SummaryResponse:
    """Get a compact summary prepared for LLM analysis."""
    user = _extract_user(request)
    ot, oid = body.owner_type, body.owner_id

    if user["id"]:
        if not await check_access(user, ot, oid):
            raise HTTPException(status_code=403, detail="Accès refusé")

    client = _get_client_for_owner(ot, oid)
    room_name = await _get_room_name(body.room_id, client)

    data = _summary.get_summary(
        room_id=body.room_id,
        room_name=room_name,
        since_hours=body.since_hours,
        max_messages=body.max_messages,
        owner_type=ot,
        owner_id=oid,
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


# ── Setup endpoints ─────────────────────────────────────────

@router.post("/setup/sso-start", response_model=SSOStartResponse)
async def setup_sso_start(body: SSOStartRequest, request: Request) -> SSOStartResponse:
    """Start the SSO login flow."""
    user = _extract_user(request)
    ot = body.owner_type
    oid = body.owner_id or user.get("id", "")

    if not await check_can_manage(user, ot, oid):
        raise HTTPException(status_code=403, detail="Vous n'avez pas les droits de gestion pour ce owner")

    result = await _setup.start_sso(ot, oid, user["id"])
    return SSOStartResponse(**result)


@router.get("/setup/sso-callback")
async def setup_sso_callback(
    loginToken: str = Query(...),
    state: str = Query(...),
) -> dict:
    """Handle the SSO callback from Matrix homeserver."""
    result = await _setup.handle_sso_callback(loginToken, state)
    if result["ok"]:
        return {"status": "ok", "message": "Connexion réussie. Vous pouvez fermer cette page."}
    raise HTTPException(status_code=400, detail=result["message"])


@router.post("/setup/sso-complete", response_model=SSOCompleteResponse)
async def setup_sso_complete(body: SSOCompleteRequest) -> SSOCompleteResponse:
    """Check if SSO callback was received."""
    result = await _setup.complete_sso(body.state)
    return SSOCompleteResponse(**result)


@router.post("/setup/login-password", response_model=LoginResponse)
async def setup_login_password(body: LoginPasswordRequest, request: Request) -> LoginResponse:
    """Login with email and password."""
    user = _extract_user(request)
    ot = body.owner_type
    oid = body.owner_id or user.get("id", "")

    if not await check_can_manage(user, ot, oid):
        raise HTTPException(status_code=403, detail="Vous n'avez pas les droits de gestion pour ce owner")

    result = await _setup.login_password(
        email=body.email,
        password=body.password,
        owner_type=ot,
        owner_id=oid,
        user_uuid=user["id"],
    )
    return LoginResponse(**result)


@router.post("/setup/login-token", response_model=LoginResponse)
async def setup_login_token(body: LoginTokenRequest, request: Request) -> LoginResponse:
    """Login with a pre-existing access token."""
    user = _extract_user(request)
    ot = body.owner_type
    oid = body.owner_id or user.get("id", "")

    if not await check_can_manage(user, ot, oid):
        raise HTTPException(status_code=403, detail="Vous n'avez pas les droits de gestion pour ce owner")

    result = await _setup.login_token(
        token=body.token,
        owner_type=ot,
        owner_id=oid,
        user_uuid=user["id"],
    )
    return LoginResponse(**result)


# ── Room management (multi-tenant) ──────────────────────────

@router.get("/discover-rooms", response_model=DiscoverRoomsResponse)
async def discover_rooms(
    request: Request,
    owner_type: str = Query("user"),
    owner_id: str = Query(""),
) -> DiscoverRoomsResponse:
    """List all rooms the account has joined on the homeserver."""
    user = _extract_user(request)
    oid = owner_id or user.get("id", "")

    if not await check_can_manage(user, owner_type, oid):
        raise HTTPException(status_code=403, detail="Accès refusé")

    account = _db.get_matrix_account(owner_type, oid)
    if not account:
        return DiscoverRoomsResponse(ok=False, message="Aucun compte configuré pour ce owner.")

    client = create_client_for_account(account)
    try:
        room_ids = await client.get_joined_rooms()
    except Exception as exc:
        return DiscoverRoomsResponse(ok=False, message=f"Erreur : {exc}")

    followed = _db.get_all_followed_room_ids(owner_type, oid)
    rooms = []
    for room_id in room_ids:
        name = await client.get_room_name(room_id)
        rooms.append({
            "room_id": room_id,
            "name": name,
            "followed": room_id in followed,
        })

    return DiscoverRoomsResponse(ok=True, rooms=rooms)


@router.get("/search-rooms")
async def search_rooms(
    request: Request,
    q: str = Query("", description="Recherche par nom (vide = tous)"),
    owner_type: str = Query("user"),
    owner_id: str = Query(""),
) -> dict:
    """Search rooms by name — fetches room names in parallel for speed."""
    import asyncio

    user = _extract_user(request)
    oid = owner_id or user.get("id", "")

    if not await check_can_manage(user, owner_type, oid):
        raise HTTPException(status_code=403, detail="Accès refusé")

    account = _db.get_matrix_account(owner_type, oid)
    if not account:
        return {"ok": False, "rooms": [], "message": "Aucun compte configuré."}

    client = create_client_for_account(account)
    try:
        room_ids = await client.get_joined_rooms()
    except Exception as exc:
        return {"ok": False, "rooms": [], "message": f"Erreur : {exc}"}

    followed = _db.get_all_followed_room_ids(owner_type, oid)

    # Fetch room names in parallel (batches of 10)
    async def _get_name(rid: str) -> dict:
        name = await client.get_room_name(rid)
        return {"room_id": rid, "name": name, "followed": rid in followed}

    rooms = []
    batch_size = 10
    for i in range(0, len(room_ids), batch_size):
        batch = room_ids[i:i + batch_size]
        results = await asyncio.gather(*[_get_name(rid) for rid in batch])
        rooms.extend(results)

    # Filter by query
    if q:
        q_lower = q.lower()
        rooms = [r for r in rooms if q_lower in r["name"].lower() or q_lower in r["room_id"].lower()]

    return {"ok": True, "rooms": rooms, "total": len(room_ids), "filtered": len(rooms)}


@router.post("/follow-room", response_model=FollowRoomResponse)
async def follow_room(body: FollowRoomRequest, request: Request) -> FollowRoomResponse:
    """Add a room to the followed list."""
    user = _extract_user(request)
    ot, oid = body.owner_type, body.owner_id or user.get("id", "")

    if not await check_can_manage(user, ot, oid):
        raise HTTPException(status_code=403, detail="Accès refusé")

    _db.follow_room(ot, oid, body.room_id, body.name, user.get("id", ""))
    followed = sorted(_db.get_all_followed_room_ids(ot, oid))
    return FollowRoomResponse(ok=True, message=f"Salon {body.room_id} ajouté.", followed_rooms=followed)


@router.post("/unfollow-room", response_model=FollowRoomResponse)
async def unfollow_room(body: FollowRoomRequest, request: Request) -> FollowRoomResponse:
    """Remove a room from the followed list."""
    user = _extract_user(request)
    ot, oid = body.owner_type, body.owner_id or user.get("id", "")

    if not await check_can_manage(user, ot, oid):
        raise HTTPException(status_code=403, detail="Accès refusé")

    _db.unfollow_room(ot, oid, body.room_id)
    followed = sorted(_db.get_all_followed_room_ids(ot, oid))
    return FollowRoomResponse(ok=True, message=f"Salon {body.room_id} retiré.", followed_rooms=followed)


# ── Admin endpoints ─────────────────────────────────────────

@router.get("/admin/all-access", response_model=AllAccessResponse)
async def admin_all_access(request: Request) -> AllAccessResponse:
    """List all configured Matrix accounts (admin only)."""
    user = _extract_user(request)
    if user["id"] and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin uniquement")

    accounts = _db.get_all_matrix_accounts()
    entries = [
        AllAccessEntry(
            owner_type=a["owner_type"],
            owner_id=a["owner_id"],
            homeserver_url=a["homeserver_url"],
            user_id=a["user_id"],
            configured_by=a["configured_by"],
            created_at=a["created_at"],
        )
        for a in accounts
    ]
    return AllAccessResponse(ok=True, entries=entries)


@router.post("/admin/set-global", response_model=AdminActionResponse)
async def admin_set_global(body: SetGlobalRequest, request: Request) -> AdminActionResponse:
    """Make a room globally accessible (admin only)."""
    user = _extract_user(request)
    if user["id"] and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin uniquement")

    # Add room to global followed rooms
    _db.follow_room("global", "global", body.room_id, "", user.get("id", "admin"))
    return AdminActionResponse(ok=True, message=f"Salon {body.room_id} ajouté en accès global.")


@router.post("/admin/revoke", response_model=AdminActionResponse)
async def admin_revoke(body: RevokeRequest, request: Request) -> AdminActionResponse:
    """Revoke a Matrix account (admin only)."""
    user = _extract_user(request)
    if user["id"] and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin uniquement")

    _db.delete_matrix_account(body.owner_type, body.owner_id)
    return AdminActionResponse(ok=True, message=f"Accès révoqué pour {body.owner_type}/{body.owner_id}.")


# ── Legacy admin endpoints (backward compat) ────────────────

@router.get("/admin/status")
async def admin_status() -> dict:
    """Get current configuration status (legacy + multi-tenant)."""
    _reload_config_from_db()
    accounts = _db.get_all_matrix_accounts()
    configured = bool(accounts) or bool(settings.TCHAP_ACCESS_TOKEN and settings.TCHAP_USER_ID)

    return {
        "configured": configured,
        "homeserver_url": settings.TCHAP_HOMESERVER_URL,
        "user_id": settings.TCHAP_USER_ID,
        "allowed_rooms": sorted(settings.allowed_rooms),
        "total_messages": _db.get_total_messages(),
        "accounts": len(accounts),
    }


@router.post("/admin/configure")
async def admin_configure(request: Request) -> dict:
    """Configure bot account (legacy endpoint — kept for backward compat)."""
    import httpx
    body = await request.json()

    homeserver_url = body.get("homeserver_url", settings.TCHAP_HOMESERVER_URL)
    user_id = body.get("user_id", "")
    access_token = body.get("access_token", "")
    device_id = body.get("device_id", "OWUI_BOT")

    # Test connection
    test_url = f"{homeserver_url.rstrip('/')}/_matrix/client/v3/joined_rooms"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(test_url, headers={"Authorization": f"Bearer {access_token}"})
        if resp.status_code == 401:
            return {"ok": False, "message": "Authentification échouée (401)."}
        resp.raise_for_status()
        joined_rooms = resp.json().get("joined_rooms", [])
    except Exception as exc:
        return {"ok": False, "message": f"Connexion échouée : {exc}"}

    # Save to DB and settings
    _db.set_config("homeserver_url", homeserver_url)
    _db.set_config("user_id", user_id)
    _db.set_config("access_token", access_token)
    _db.set_config("device_id", device_id)

    settings.TCHAP_HOMESERVER_URL = homeserver_url
    settings.TCHAP_USER_ID = user_id
    settings.TCHAP_ACCESS_TOKEN = access_token
    settings.TCHAP_DEVICE_ID = device_id

    # Also save as a global Matrix account
    _db.save_matrix_account(
        owner_type="global",
        owner_id="global",
        homeserver_url=homeserver_url,
        user_id=user_id,
        access_token=access_token,
        device_id=device_id,
        configured_by="admin",
    )

    return {
        "ok": True,
        "message": f"Compte configuré. {len(joined_rooms)} salon(s) rejoint(s).",
        "user_id": user_id,
        "joined_rooms": joined_rooms,
    }


@router.get("/admin/discover-rooms")
async def admin_discover_rooms_legacy() -> dict:
    """List bot's joined rooms (legacy endpoint)."""
    if not settings.TCHAP_ACCESS_TOKEN:
        return {"ok": False, "message": "Compte non configuré."}

    try:
        room_ids = await _default_client.get_joined_rooms()
    except Exception as exc:
        return {"ok": False, "message": f"Erreur : {exc}"}

    rooms = []
    for room_id in room_ids:
        name = await _default_client.get_room_name(room_id)
        is_followed = room_id in settings.allowed_rooms
        rooms.append({"room_id": room_id, "name": name, "followed": is_followed})

    return {"ok": True, "rooms": rooms}


@router.post("/admin/follow-room")
async def admin_follow_room_legacy(request: Request) -> dict:
    """Add room to followed list (legacy endpoint)."""
    body = await request.json()
    room_id = body.get("room_id", "")
    name = body.get("name", "")

    current = settings.allowed_rooms
    current.add(room_id)
    new_list = ",".join(sorted(current))
    settings.TCHAP_ALLOWED_ROOM_IDS = new_list
    _db.set_config("allowed_room_ids", new_list)

    if name:
        _room_names[room_id] = name

    # Also add to global followed rooms
    _db.follow_room("global", "global", room_id, name, "admin")

    return {"ok": True, "message": f"Salon {room_id} ajouté.", "allowed_rooms": sorted(current)}


@router.post("/admin/unfollow-room")
async def admin_unfollow_room_legacy(request: Request) -> dict:
    """Remove room from followed list (legacy endpoint)."""
    body = await request.json()
    room_id = body.get("room_id", "")

    current = settings.allowed_rooms
    current.discard(room_id)
    new_list = ",".join(sorted(current))
    settings.TCHAP_ALLOWED_ROOM_IDS = new_list
    _db.set_config("allowed_room_ids", new_list)

    _db.unfollow_room("global", "global", room_id)

    return {"ok": True, "message": f"Salon {room_id} retiré.", "allowed_rooms": sorted(current)}


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


# Load persisted config on module import
_reload_config_from_db()
