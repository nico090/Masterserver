import os
from dotenv import load_dotenv

load_dotenv()

VPS_PUBLIC_IP = os.getenv("VPS_PUBLIC_IP", "127.0.0.1")
GAME_SERVER_PATH = os.getenv("GAME_SERVER_PATH", "/opt/gameserver/BossRoom.x86_64")
GAME_SERVER_DIR = os.getenv("GAME_SERVER_DIR", "")  # working dir for the server process
PORT_RANGE_START = int(os.getenv("PORT_RANGE_START", "7770"))
PORT_RANGE_END = int(os.getenv("PORT_RANGE_END", "7870"))
# URL que el game server usa para enviar heartbeats al master server.
MASTER_SERVER_INTERNAL_URL = os.getenv("MASTER_SERVER_INTERNAL_URL", "http://127.0.0.1:8000")
SERVER_SECRET = os.getenv("SERVER_SECRET", "change-me-in-production")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "change-me")

# Timeouts (seconds)
HEARTBEAT_TIMEOUT_SECONDS = 45
EMPTY_ROOM_TIMEOUT_SECONDS = 120
STARTING_TIMEOUT_SECONDS = 60
PENDING_KEY_TIMEOUT_SECONDS = 90  # keys expire after 90s — creator gets key at room creation but server needs ~5-8s to start

# Pre-created rooms
NUM_ROOMS = int(os.getenv("NUM_ROOMS", "5"))
DEFAULT_ROOM_NAMES = [
    "Sala 1", "Sala 2", "Sala 3", "Sala 4", "Sala 5",
]
DEFAULT_MAX_PLAYERS = int(os.getenv("DEFAULT_MAX_PLAYERS", "8"))

# Logs
LOG_DIR = os.getenv("LOG_DIR", "logs")

# Database
DB_PATH = os.getenv("DB_PATH", "master_server.db")
