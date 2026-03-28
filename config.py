import os
from dotenv import load_dotenv

load_dotenv()

VPS_PUBLIC_IP = os.getenv("VPS_PUBLIC_IP", "127.0.0.1")
GAME_SERVER_PATH = os.getenv("GAME_SERVER_PATH", "/opt/gameserver/BossRoom.x86_64")
GAME_SERVER_DIR = os.getenv("GAME_SERVER_DIR", "")  # working dir for the server process
PORT_RANGE_START = int(os.getenv("PORT_RANGE_START", "7770"))
PORT_RANGE_END = int(os.getenv("PORT_RANGE_END", "7870"))
# URL que el game server usa para enviar heartbeats al master server.
# Dentro del mismo contenedor Docker usa 127.0.0.1. Si corre en otro contenedor
# usar el nombre del servicio, e.g. http://master-server:8000
MASTER_SERVER_INTERNAL_URL = os.getenv("MASTER_SERVER_INTERNAL_URL", "http://127.0.0.1:8000")
SERVER_SECRET = os.getenv("SERVER_SECRET", "change-me-in-production")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "change-me")

# URL of host_agent.py running on the host machine.
# Since docker-compose uses network_mode: host, 127.0.0.1 reaches the host directly.
HOST_AGENT_URL = os.getenv("HOST_AGENT_URL", "http://127.0.0.1:8099")

# Timeouts (seconds)
HEARTBEAT_TIMEOUT_SECONDS = 45
EMPTY_ROOM_TIMEOUT_SECONDS = 120
STARTING_TIMEOUT_SECONDS = 60
PENDING_KEY_TIMEOUT_SECONDS = 30  # keys expire after 30s if not consumed

# Rate limiting
MAX_ROOMS_PER_MINUTE = int(os.getenv("MAX_ROOMS_PER_MINUTE", "5"))

# Logs
LOG_DIR = os.getenv("LOG_DIR", "logs")

# Database
DB_PATH = os.getenv("DB_PATH", "master_server.db")
