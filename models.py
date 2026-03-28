from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field


class RoomStatus(str, Enum):
    STARTING = "starting"
    READY = "ready"
    IN_GAME = "in_game"
    CLOSING = "closing"


class RoomCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    password: str | None = None
    max_players: int = Field(default=8, ge=2, le=16)
    creator_name: str | None = None  # Name of the player creating the room


class RoomInfo(BaseModel):
    room_id: str
    name: str
    has_password: bool
    current_players: int
    max_players: int
    host_address: str
    port: int
    status: RoomStatus
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
    error: str | None = None


class CreateRoomResponse(BaseModel):
    room_id: str
    name: str
    port: int
    max_players: int
    host_address: str
    room_key: str  # For the creator to join immediately


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
