"""
Async SQLite persistence layer for the master server.

Rooms are stored in-memory (RoomManager) for speed and synced here for
durability. On startup, stale rooms (no live process) are pruned automatically.

To switch to MySQL, replace the aiosqlite calls with aiomysql and adjust
the SQL dialect (e.g. TEXT PRIMARY KEY → VARCHAR(32) PRIMARY KEY, ? → %s).
"""

import aiosqlite
import logging
from datetime import datetime, timezone

import config

logger = logging.getLogger("master_server.db")

DB_PATH = getattr(config, "DB_PATH", "master_server.db")


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS rooms (
                room_id      TEXT PRIMARY KEY,
                name         TEXT NOT NULL,
                port         INTEGER NOT NULL,
                max_players  INTEGER NOT NULL,
                status       TEXT NOT NULL,
                current_players INTEGER NOT NULL DEFAULT 0,
                created_at   TEXT NOT NULL,
                last_heartbeat TEXT NOT NULL
            )
        """)
        await db.commit()
    logger.info(f"Database ready at {DB_PATH}")


async def db_upsert_room(room_id: str, name: str, port: int, max_players: int,
                         status: str, current_players: int,
                         created_at: datetime, last_heartbeat: datetime):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO rooms
                (room_id, name, port, max_players, status, current_players, created_at, last_heartbeat)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(room_id) DO UPDATE SET
                status          = excluded.status,
                current_players = excluded.current_players,
                last_heartbeat  = excluded.last_heartbeat
        """, (
            room_id, name, port, max_players, status, current_players,
            created_at.isoformat(), last_heartbeat.isoformat(),
        ))
        await db.commit()


async def db_delete_room(room_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM rooms WHERE room_id = ?", (room_id,))
        await db.commit()


async def db_load_rooms() -> list[dict]:
    """Return all persisted rooms as plain dicts."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM rooms") as cursor:
            rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def db_clear_all_rooms():
    """Called on clean shutdown — removes all rooms from the DB."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM rooms")
        await db.commit()
    logger.info("All rooms cleared from database")
