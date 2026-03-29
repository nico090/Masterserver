# BossRoom Master Server

FastAPI-based matchmaking and game server orchestration service.

## Setup

### Local (without Docker)

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # Edit with your settings
python main.py         # Starts on http://0.0.0.0:8000
```

### Docker

```bash
docker compose up -d --build
```

Uses `network_mode: host` so the container shares the host's network stack (required for spawning game servers on host ports).

### Host Agent

The host agent must run **on the host machine** (not inside Docker). It spawns game server processes as `screen` sessions:

```bash
python host_agent.py
# Listens on 127.0.0.1:8099
```

Prerequisites: `screen` (`apt install screen` on Debian/Ubuntu).

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VPS_PUBLIC_IP` | `127.0.0.1` | Public IP advertised to clients |
| `GAME_SERVER_PATH` | `/opt/gameserver/BossRoom.x86_64` | Path to game server binary |
| `GAME_SERVER_DIR` | (empty) | Working directory for game server process |
| `PORT_RANGE_START` | `7770` | First port for game servers |
| `PORT_RANGE_END` | `7870` | Last port for game servers |
| `SERVER_SECRET` | `change-me-in-production` | Shared secret between master and game servers |
| `ADMIN_API_KEY` | `change-me` | Key for admin endpoints |
| `MASTER_SERVER_INTERNAL_URL` | `http://127.0.0.1:8000` | URL game servers use to reach master server |
| `HOST_AGENT_URL` | `http://127.0.0.1:8099` | URL of host_agent.py |
| `MAX_ROOMS_PER_MINUTE` | `5` | Rate limit per client IP |
| `LOG_DIR` | `logs` | Directory for game server log files |
| `DB_PATH` | `master_server.db` | SQLite database path |

## API Endpoints

### Client-facing

#### `GET /api/health`
Health check.

**Response:** `{"status": "ok"}`

---

#### `GET /api/rooms`
List all active rooms (excludes rooms with status `"closing"`).

**Response:**
```json
[
  {
    "room_id": "a1b2c3d4e5f6",
    "name": "My Room",
    "has_password": false,
    "current_players": 2,
    "max_players": 8,
    "host_address": "203.0.113.10",
    "port": 7770,
    "status": "ready",
    "created_at": "2026-03-29T12:00:00+00:00"
  }
]
```

---

#### `POST /api/rooms`
Create a new room. Spawns a dedicated game server.

**Request:**
```json
{
  "name": "My Room",
  "password": null,
  "max_players": 8,
  "creator_name": "Player1"
}
```

**Response:**
```json
{
  "room_id": "a1b2c3d4e5f6",
  "name": "My Room",
  "port": 7770,
  "max_players": 8,
  "host_address": "203.0.113.10",
  "room_key": "one-time-use-token"
}
```

The `room_key` allows the creator to connect to the game server without going through the join flow.

---

#### `POST /api/rooms/join`
Join an existing room. Validates password and capacity.

**Request:**
```json
{
  "room_id": "a1b2c3d4e5f6",
  "password": null,
  "player_name": "Player2"
}
```

**Response:**
```json
{
  "success": true,
  "host_address": "203.0.113.10",
  "port": 7770,
  "room_key": "one-time-use-token",
  "error": null
}
```

---

#### `GET /api/rooms/{room_id}/status`
Lightweight endpoint to check a single room's status (used for polling during room creation).

**Response:**
```json
{
  "room_id": "a1b2c3d4e5f6",
  "status": "ready",
  "current_players": 1
}
```

---

### Admin

#### `DELETE /api/rooms/{room_id}`
Delete a room and kill its game server. Requires `X-Admin-Key` header.

#### `GET /api/stats`
Server statistics. Requires `X-Admin-Key` header.

**Response:**
```json
{
  "total_rooms": 5,
  "active_rooms": 3,
  "total_players": 12,
  "available_ports": 97
}
```

---

### Game Server -> Master Server

#### `POST /api/heartbeat`
Sent by dedicated servers every 3 seconds. Requires `server_secret`.

```json
{
  "room_id": "a1b2c3d4e5f6",
  "server_secret": "shared-secret",
  "current_players": 2,
  "status": "ready"
}
```

#### `POST /api/validate-key`
Validate a one-time room key when a client connects. Requires `server_secret`.

```json
{
  "room_id": "a1b2c3d4e5f6",
  "room_key": "token-from-client",
  "server_secret": "shared-secret"
}
```

**Response:** `{"valid": true, "player_name": "Player1"}` or `{"valid": false}`

## Room Lifecycle

```
  [Create Room]
       |
       v
   "starting"  -- server process spawning, no heartbeat yet
       |
       | (first heartbeat with status="ready")
       v
    "ready"    -- accepting connections
       |
       | (game begins)
       v
   "in_game"   -- match in progress
       |
       | (all players leave or server sends status="closing")
       v
   "closing"   -- hidden from room list, about to be cleaned up
       |
       v
   [Deleted]
```

## Timeouts

| Event | Timeout | Action |
|-------|---------|--------|
| No heartbeat received | 45s | Kill server, delete room |
| Room stuck in "starting" | 60s | Kill server, delete room |
| Room empty (0 players, status "ready") | 120s | Kill server, delete room |
| Room key not consumed | 90s | Key expires (room unaffected) |

The cleanup loop runs every 30 seconds.
