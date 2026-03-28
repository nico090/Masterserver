import secrets
import threading
import uuid
from datetime import datetime, timezone

import bcrypt

import config


class PendingKey:
    __slots__ = ("player_name", "created_at")

    def __init__(self, player_name: str):
        self.player_name = player_name
        self.created_at = datetime.now(timezone.utc)


class Room:
    __slots__ = (
        "room_id", "name", "password_hash", "max_players",
        "current_players", "port", "status", "created_at",
        "last_heartbeat", "pending_keys",
    )

    def __init__(self, name: str, password: str | None, max_players: int, port: int):
        self.room_id: str = uuid.uuid4().hex[:12]
        self.name = name
        self.password_hash: bytes | None = (
            bcrypt.hashpw(password.encode(), bcrypt.gensalt()) if password else None
        )
        self.max_players = max_players
        self.current_players = 0
        self.port = port
        self.status = "starting"
        now = datetime.now(timezone.utc)
        self.created_at = now
        self.last_heartbeat = now
        self.pending_keys: dict[str, PendingKey] = {}

    @property
    def has_password(self) -> bool:
        return self.password_hash is not None

    def check_password(self, password: str | None) -> bool:
        if self.password_hash is None:
            return True
        if password is None:
            return False
        return bcrypt.checkpw(password.encode(), self.password_hash)

    def generate_key(self, player_name: str) -> str:
        self.purge_expired_keys()
        key = secrets.token_urlsafe(32)
        self.pending_keys[key] = PendingKey(player_name)
        return key

    def consume_key(self, key: str) -> str | None:
        """Validate and consume a one-time join key. Returns player_name or None."""
        pk = self.pending_keys.pop(key, None)
        if pk is None:
            return None
        # Reject expired keys
        elapsed = (datetime.now(timezone.utc) - pk.created_at).total_seconds()
        if elapsed > config.PENDING_KEY_TIMEOUT_SECONDS:
            return None
        return pk.player_name

    def purge_expired_keys(self):
        now = datetime.now(timezone.utc)
        expired = [
            k for k, pk in self.pending_keys.items()
            if (now - pk.created_at).total_seconds() > config.PENDING_KEY_TIMEOUT_SECONDS
        ]
        for k in expired:
            del self.pending_keys[k]

    def to_info(self) -> dict:
        return {
            "room_id": self.room_id,
            "name": self.name,
            "has_password": self.has_password,
            "current_players": self.current_players,
            "max_players": self.max_players,
            "host_address": config.VPS_PUBLIC_IP,
            "port": self.port,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
        }


class RoomManager:
    def __init__(self):
        self._rooms: dict[str, Room] = {}
        self._used_ports: set[int] = set()
        self._lock = threading.Lock()

    def _allocate_port(self) -> int | None:
        for port in range(config.PORT_RANGE_START, config.PORT_RANGE_END + 1):
            if port not in self._used_ports:
                self._used_ports.add(port)
                return port
        return None

    def _free_port(self, port: int):
        self._used_ports.discard(port)

    @property
    def available_ports(self) -> int:
        total = config.PORT_RANGE_END - config.PORT_RANGE_START + 1
        return total - len(self._used_ports)

    def create_room(self, name: str, password: str | None, max_players: int) -> Room | None:
        with self._lock:
            port = self._allocate_port()
            if port is None:
                return None
            room = Room(name, password, max_players, port)
            self._rooms[room.room_id] = room
            return room

    def get_room(self, room_id: str) -> Room | None:
        return self._rooms.get(room_id)

    def remove_room(self, room_id: str):
        with self._lock:
            room = self._rooms.pop(room_id, None)
            if room:
                self._free_port(room.port)

    def list_rooms(self) -> list[dict]:
        return [
            r.to_info()
            for r in list(self._rooms.values())
            if r.status != "closing"
        ]

    def all_rooms(self) -> list[Room]:
        return list(self._rooms.values())

    def stats(self) -> dict:
        rooms = list(self._rooms.values())
        active = [r for r in rooms if r.status in ("ready", "in_game")]
        return {
            "total_rooms": len(rooms),
            "active_rooms": len(active),
            "total_players": sum(r.current_players for r in active),
            "available_ports": self.available_ports,
        }
