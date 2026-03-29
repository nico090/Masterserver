import asyncio
import logging
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Header, Request

import config
from database import db_clear_all_rooms, db_delete_room, db_load_rooms, db_upsert_room, init_db
from models import (
    CreateRoomResponse,
    HeartbeatRequest,
    JoinRequest,
    JoinResponse,
    RoomCreate,
    RoomInfo,
    ServerStats,
    ValidateKeyRequest,
    ValidateKeyResponse,
)
from process_manager import ProcessManager
from room_manager import RoomManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("master_server")

room_mgr = RoomManager()
proc_mgr = ProcessManager()

# Simple in-memory rate limiter: ip -> list of timestamps
_create_timestamps: dict[str, list[float]] = defaultdict(list)


# ---------------------------------------------------------------------------
# Cleanup background task
# ---------------------------------------------------------------------------

async def cleanup_loop():
    while True:
        await asyncio.sleep(30)
        now = datetime.now(timezone.utc)

        # 1. Remove rooms whose process died
        dead = proc_mgr.cleanup_dead()
        for room_id in dead:
            logger.warning(f"Server process died for room {room_id}")
            room_mgr.remove_room(room_id)
            await db_delete_room(room_id)

        # 2. Check heartbeat timeouts and stale rooms
        for room in room_mgr.all_rooms():
            elapsed = (now - room.last_heartbeat).total_seconds()

            if room.status == "starting" and elapsed > config.STARTING_TIMEOUT_SECONDS:
                logger.warning(f"Room {room.room_id} stuck in starting, killing")
                proc_mgr.kill_server(room.room_id)
                room_mgr.remove_room(room.room_id)
                await db_delete_room(room.room_id)

            elif room.status == "ready" and room.current_players == 0 and elapsed > config.EMPTY_ROOM_TIMEOUT_SECONDS:
                logger.info(f"Room {room.room_id} empty too long, killing")
                proc_mgr.kill_server(room.room_id)
                room_mgr.remove_room(room.room_id)
                await db_delete_room(room.room_id)

            elif elapsed > config.HEARTBEAT_TIMEOUT_SECONDS and room.status != "starting":
                logger.warning(f"Room {room.room_id} heartbeat timeout ({elapsed:.0f}s)")
                proc_mgr.kill_server(room.room_id)
                room_mgr.remove_room(room.room_id)
                await db_delete_room(room.room_id)

            else:
                # Purge expired pending keys while we're at it
                room.purge_expired_keys()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    # Restore rooms that were alive before a restart.
    # If the process is gone (crashed master server), prune the stale entry.
    for row in await db_load_rooms():
        if not proc_mgr.is_alive(row["room_id"]):
            logger.info(f"Pruning stale room {row['room_id']} from DB (no live process)")
            await db_delete_room(row["room_id"])
        else:
            logger.info(f"Restored room {row['room_id']} from DB")

    task = asyncio.create_task(cleanup_loop())
    logger.info(f"Master server starting — ports {config.PORT_RANGE_START}-{config.PORT_RANGE_END}")
    yield
    task.cancel()
    proc_mgr.kill_all()
    await db_clear_all_rooms()
    logger.info("Master server shut down, all game servers killed")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="BossRoom Master Server", lifespan=lifespan)


def _check_rate_limit(client_ip: str):
    """Raise 429 if client has created too many rooms recently."""
    now = time.monotonic()
    window = 60.0
    timestamps = _create_timestamps[client_ip]
    # Prune old entries
    _create_timestamps[client_ip] = [t for t in timestamps if now - t < window]
    if len(_create_timestamps[client_ip]) >= config.MAX_ROOMS_PER_MINUTE:
        raise HTTPException(429, "Too many rooms created, try again later")
    _create_timestamps[client_ip].append(now)


# ---------------------------------------------------------------------------
# Client-facing endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/rooms", response_model=list[RoomInfo])
async def list_rooms():
    return room_mgr.list_rooms()


@app.post("/api/rooms", response_model=CreateRoomResponse)
async def create_room(body: RoomCreate, request: Request):
    _check_rate_limit(request.client.host)

    room = room_mgr.create_room(body.name, body.password, body.max_players)
    if room is None:
        raise HTTPException(503, "No available ports — server is full")

    ok = proc_mgr.spawn_server(room.room_id, room.port, room.max_players)
    if not ok:
        room_mgr.remove_room(room.room_id)
        raise HTTPException(500, "Failed to start game server")

    await db_upsert_room(
        room.room_id, room.name, room.port, room.max_players,
        room.status, room.current_players, room.created_at, room.last_heartbeat,
    )

    # Generate a room key for the creator to join immediately
    creator_name = body.creator_name or "Creator"
    room_key = room.generate_key(creator_name)

    logger.info(f"Room created: {room.room_id} '{room.name}' on port {room.port}")
    return CreateRoomResponse(
        room_id=room.room_id,
        name=room.name,
        port=room.port,
        max_players=room.max_players,
        host_address=config.VPS_PUBLIC_IP,
        room_key=room_key,
    )


@app.post("/api/rooms/join", response_model=JoinResponse)
async def join_room(body: JoinRequest):
    room = room_mgr.get_room(body.room_id)
    if room is None:
        return JoinResponse(success=False, error="Room not found")

    if room.status not in ("ready", "in_game"):
        return JoinResponse(success=False, error="Room is not ready yet")

    if room.current_players >= room.max_players:
        return JoinResponse(success=False, error="Room is full")

    if not room.check_password(body.password):
        return JoinResponse(success=False, error="Wrong password")

    room_key = room.generate_key(body.player_name)
    logger.info(f"Player '{body.player_name}' joining room {room.room_id}")

    return JoinResponse(
        success=True,
        host_address=config.VPS_PUBLIC_IP,
        port=room.port,
        room_key=room_key,
    )


@app.get("/api/rooms/{room_id}/status")
async def room_status(room_id: str):
    room = room_mgr.get_room(room_id)
    if room is None:
        raise HTTPException(404, "Room not found")
    return {"room_id": room_id, "status": room.status, "current_players": room.current_players}


@app.delete("/api/rooms/{room_id}")
async def delete_room(room_id: str, x_admin_key: str = Header()):
    if x_admin_key != config.ADMIN_API_KEY:
        raise HTTPException(403, "Invalid admin key")
    room = room_mgr.get_room(room_id)
    if room is None:
        raise HTTPException(404, "Room not found")
    proc_mgr.kill_server(room_id)
    room_mgr.remove_room(room_id)
    await db_delete_room(room_id)
    logger.info(f"Room {room_id} deleted by admin")
    return {"deleted": True}


@app.get("/api/stats", response_model=ServerStats)
async def stats(x_admin_key: str = Header()):
    if x_admin_key != config.ADMIN_API_KEY:
        raise HTTPException(403, "Invalid admin key")
    return room_mgr.stats()


# ---------------------------------------------------------------------------
# Game server → master server endpoints
# ---------------------------------------------------------------------------

@app.post("/api/heartbeat")
async def heartbeat(body: HeartbeatRequest):
    if body.server_secret != config.SERVER_SECRET:
        raise HTTPException(403, "Invalid server secret")
    room = room_mgr.get_room(body.room_id)
    if room is None:
        raise HTTPException(404, "Room not found")
    room.current_players = body.current_players
    room.status = body.status
    room.last_heartbeat = datetime.now(timezone.utc)
    await db_upsert_room(
        room.room_id, room.name, room.port, room.max_players,
        room.status, room.current_players, room.created_at, room.last_heartbeat,
    )
    return {"ok": True}


@app.post("/api/validate-key", response_model=ValidateKeyResponse)
async def validate_key(body: ValidateKeyRequest):
    if body.server_secret != config.SERVER_SECRET:
        raise HTTPException(403, "Invalid server secret")
    room = room_mgr.get_room(body.room_id)
    if room is None:
        return ValidateKeyResponse(valid=False)
    player_name = room.consume_key(body.room_key)
    if player_name is None:
        return ValidateKeyResponse(valid=False)
    return ValidateKeyResponse(valid=True, player_name=player_name)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
