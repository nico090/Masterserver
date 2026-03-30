from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field


class RoomStatus(str, Enum):
    STARTING = "starting"
    READY = "ready"
    IN_GAME = "in_game"
    CLOSING = "closing"


class RoomInfo(BaseModel):
    room_id: str
    name: str
    has_password: bool
    is_locked: bool
    current_players: int
    max_players: int
    host_address: str
    port: int
    status: RoomStatus
    admin_player: str | None = None
    created_at: str  # ISO 8601 string for JSON compatibility


class JoinRequest(BaseModel):
    room_id: str
    password: str | None = None
    player_name: str = Field(min_length=1, max_length=32)


class JoinResponse(BaseModel):
    success: bool
    host_address: str | None = None
    port: int | None = None
    room_key: str | None = None
    is_admin: bool = False
    error: str | None = None


class SetPrivateRequest(BaseModel):
    room_id: str
    player_name: str  # must match admin
    password: str | None = None  # password to set (None to remove / unlock)


class StartGameRequest(BaseModel):
    room_id: str
    player_name: str  # must match admin


class HeartbeatRequest(BaseModel):
    room_id: str
    server_secret: str
    current_players: int = Field(ge=0)
    status: RoomStatus


class ValidateKeyRequest(BaseModel):
    room_id: str
    room_key: str
    server_secret: str


class ValidateKeyResponse(BaseModel):
    valid: bool
    player_name: str | None = None


class ServerStats(BaseModel):
    total_rooms: int
    active_rooms: int
    total_players: int
    available_ports: int
