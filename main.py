import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Header

import config
from database import db_clear_all_rooms, db_delete_room, db_load_rooms, db_upsert_room, init_db
from models import (
    HeartbeatRequest,
    JoinRequest,
    JoinResponse,
    RoomInfo,
    ServerStats,
    SetPrivateRequest,
    StartGameRequest,
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


# ---------------------------------------------------------------------------
# Pre-create rooms on startup
# ---------------------------------------------------------------------------

async def _precreate_rooms():
    """Create the fixed set of rooms and spawn their game servers."""
    for i in range(config.NUM_ROOMS):
        name = config.DEFAULT_ROOM_NAMES[i] if i < len(config.DEFAULT_ROOM_NAMES) else f"Sala {i + 1}"
        room = room_mgr.create_room(name, config.DEFAULT_MAX_PLAYERS)
        if room is None:
            logger.error(f"Could not allocate port for room #{i + 1}")
            continue

        ok = proc_mgr.spawn_server(room.room_id, room.port, room.max_players)
        if not ok:
            logger.error(f"Failed to start game server for room '{name}'")
            room_mgr.remove_room(room.room_id)
            continue

        await db_upsert_room(
            room.room_id, room.name, room.port, room.max_players,
            room.status, room.current_players, room.created_at, room.last_heartbeat,
            room.admin_player,
        )
        logger.info(f"Pre-created room '{name}' ({room.room_id}) on port {room.port}")


# ---------------------------------------------------------------------------
# Cleanup background task
# ---------------------------------------------------------------------------

async def cleanup_loop():
    while True:
        await asyncio.sleep(30)
        now = datetime.now(timezone.utc)

        # 1. Remove rooms whose process died — respawn them
        dead = proc_mgr.cleanup_dead()
        for room_id in dead:
            logger.warning(f"Server process died for room {room_id}, removing and respawning")
            old_room = room_mgr.get_room(room_id)
            old_name = old_room.name if old_room else "Sala"
            room_mgr.remove_room(room_id)
            await db_delete_room(room_id)

            # Respawn a replacement room
            new_room = room_mgr.create_room(old_name, config.DEFAULT_MAX_PLAYERS)
            if new_room:
                ok = proc_mgr.spawn_server(new_room.room_id, new_room.port, new_room.max_players)
                if ok:
                    await db_upsert_room(
                        new_room.room_id, new_room.name, new_room.port, new_room.max_players,
                        new_room.status, new_room.current_players, new_room.created_at,
                        new_room.last_heartbeat, new_room.admin_player,
                    )
                    logger.info(f"Respawned room '{old_name}' as {new_room.room_id}")
                else:
                    room_mgr.remove_room(new_room.room_id)

        # 2. Check heartbeat timeouts and stale rooms
        for room in room_mgr.all_rooms():
            elapsed = (now - room.last_heartbeat).total_seconds()

            if room.status == "starting" and elapsed > config.STARTING_TIMEOUT_SECONDS:
                logger.warning(f"Room {room.room_id} stuck in starting, killing and respawning")
                old_name = room.name
                proc_mgr.kill_server(room.room_id)
                room_mgr.remove_room(room.room_id)
                await db_delete_room(room.room_id)
                # Respawn
                new_room = room_mgr.create_room(old_name, config.DEFAULT_MAX_PLAYERS)
                if new_room:
                    ok = proc_mgr.spawn_server(new_room.room_id, new_room.port, new_room.max_players)
                    if ok:
                        await db_upsert_room(
                            new_room.room_id, new_room.name, new_room.port, new_room.max_players,
                            new_room.status, new_room.current_players, new_room.created_at,
                            new_room.last_heartbeat, new_room.admin_player,
                        )
                    else:
                        room_mgr.remove_room(new_room.room_id)

            elif room.status == "ready" and room.current_players == 0 and elapsed > config.EMPTY_ROOM_TIMEOUT_SECONDS:
                # Room is empty for too long — reset it (clear admin, password) instead of killing
                logger.info(f"Room {room.room_id} empty too long, resetting state")
                room.reset()

            elif elapsed > config.HEARTBEAT_TIMEOUT_SECONDS and room.status != "starting":
                logger.warning(f"Room {room.room_id} heartbeat timeout ({elapsed:.0f}s), killing and respawning")
                old_name = room.name
                proc_mgr.kill_server(room.room_id)
                room_mgr.remove_room(room.room_id)
                await db_delete_room(room.room_id)
                # Respawn
                new_room = room_mgr.create_room(old_name, config.DEFAULT_MAX_PLAYERS)
                if new_room:
                    ok = proc_mgr.spawn_server(new_room.room_id, new_room.port, new_room.max_players)
                    if ok:
                        await db_upsert_room(
                            new_room.room_id, new_room.name, new_room.port, new_room.max_players,
                            new_room.status, new_room.current_players, new_room.created_at,
                            new_room.last_heartbeat, new_room.admin_player,
                        )
                    else:
                        room_mgr.remove_room(new_room.room_id)

            else:
                room.purge_expired_keys()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    # Restore rooms that were alive before a restart.
    for row in await db_load_rooms():
        if not proc_mgr.is_alive(row["room_id"]):
            logger.info(f"Pruning stale room {row['room_id']} from DB (no live process)")
            await db_delete_room(row["room_id"])
        else:
            logger.info(f"Restored room {row['room_id']} from DB")

    # Ensure we always have NUM_ROOMS rooms running
    current_count = len(room_mgr.all_rooms())
    rooms_to_create = config.NUM_ROOMS - current_count
    if rooms_to_create > 0:
        logger.info(f"Need to create {rooms_to_create} rooms (have {current_count}/{config.NUM_ROOMS})")
        for i in range(rooms_to_create):
            idx = current_count + i
            name = config.DEFAULT_ROOM_NAMES[idx] if idx < len(config.DEFAULT_ROOM_NAMES) else f"Sala {idx + 1}"
            room = room_mgr.create_room(name, config.DEFAULT_MAX_PLAYERS)
            if room is None:
                logger.error(f"Could not allocate port for room #{idx + 1}")
                continue
            ok = proc_mgr.spawn_server(room.room_id, room.port, room.max_players)
            if not ok:
                logger.error(f"Failed to start game server for room '{name}'")
                room_mgr.remove_room(room.room_id)
                continue
            await db_upsert_room(
                room.room_id, room.name, room.port, room.max_players,
                room.status, room.current_players, room.created_at, room.last_heartbeat,
                room.admin_player,
            )
            logger.info(f"Pre-created room '{name}' ({room.room_id}) on port {room.port}")

    task = asyncio.create_task(cleanup_loop())
    logger.info(f"Master server starting — {config.NUM_ROOMS} rooms, ports {config.PORT_RANGE_START}-{config.PORT_RANGE_END}")
    yield
    task.cancel()
    proc_mgr.kill_all()
    await db_clear_all_rooms()
    logger.info("Master server shut down, all game servers killed")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="BossRoom Master Server", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Client-facing endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/rooms", response_model=list[RoomInfo])
async def list_rooms():
    return room_mgr.list_rooms()


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

    # First player to join becomes admin (player 0)
    is_admin = False
    if room.admin_player is None:
        room.admin_player = body.player_name
        is_admin = True
        logger.info(f"Player '{body.player_name}' is now admin of room {room.room_id}")

    room_key = room.generate_key(body.player_name)
    logger.info(f"Player '{body.player_name}' joining room {room.room_id} (admin={is_admin})")

    return JoinResponse(
        success=True,
        host_address=config.VPS_PUBLIC_IP,
        port=room.port,
        room_key=room_key,
        is_admin=is_admin,
    )


@app.post("/api/rooms/{room_id}/set-private")
async def set_room_private(room_id: str, body: SetPrivateRequest):
    room = room_mgr.get_room(room_id)
    if room is None:
        raise HTTPException(404, "Room not found")

    if room.admin_player != body.player_name:
        raise HTTPException(403, "Only the room admin can change privacy")

    room.set_password(body.password)
    logger.info(f"Room {room_id} set to {'private' if room.is_locked else 'public'} by {body.player_name}")
    return {"success": True, "is_locked": room.is_locked}


@app.post("/api/rooms/{room_id}/start")
async def start_game(room_id: str, body: StartGameRequest):
    room = room_mgr.get_room(room_id)
    if room is None:
        raise HTTPException(404, "Room not found")

    if room.admin_player != body.player_name:
        raise HTTPException(403, "Only the room admin can start the game")

    if room.status != "ready":
        raise HTTPException(400, f"Room is not ready (current status: {room.status})")

    if room.current_players < 1:
        raise HTTPException(400, "Need at least 1 player to start")

    room.status = "in_game"
    logger.info(f"Game started in room {room_id} by admin {body.player_name}")
    return {"success": True, "status": "in_game"}


@app.get("/api/rooms/{room_id}/status")
async def room_status(room_id: str):
    room = room_mgr.get_room(room_id)
    if room is None:
        raise HTTPException(404, "Room not found")
    return {
        "room_id": room_id,
        "status": room.status,
        "current_players": room.current_players,
        "admin_player": room.admin_player,
        "is_locked": room.is_locked,
    }


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

    # If room goes empty during gameplay, reset admin so next joiner becomes admin
    if room.current_players == 0 and room.admin_player is not None:
        logger.info(f"Room {room.room_id} is now empty, resetting admin")
        room.reset()

    await db_upsert_room(
        room.room_id, room.name, room.port, room.max_players,
        room.status, room.current_players, room.created_at, room.last_heartbeat,
        room.admin_player,
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
