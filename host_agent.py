#!/usr/bin/env python3
"""
host_agent.py — runs on the HOST (not inside Docker).

The master server container communicates with this agent to spawn and kill
game server processes as screen sessions directly on the host machine.

Usage:
    python3 host_agent.py

Environment variables:
    HOST_AGENT_PORT  — port to listen on (default: 8099)
"""
import json
import logging
import os
import shlex
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

BIND_HOST = "127.0.0.1"
BIND_PORT = int(os.getenv("HOST_AGENT_PORT", "8099"))
SCREEN_PREFIX = "gameserver_"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("host_agent")


# ---------------------------------------------------------------------------
# Screen helpers
# ---------------------------------------------------------------------------

def _screen_name(room_id: str) -> str:
    return f"{SCREEN_PREFIX}{room_id}"


def _screen_is_alive(room_id: str) -> bool:
    sname = _screen_name(room_id)
    result = subprocess.run(
        ["screen", "-ls", sname],
        capture_output=True, text=True,
    )
    return sname in result.stdout


def _spawn(room_id: str, port: int, max_players: int,
           server_path: str, server_dir: str,
           logs_dir: str, env_vars: dict) -> dict:

    if not os.path.isfile(server_path):
        return {"ok": False, "error": f"Binary not found: {server_path}"}

    os.makedirs(logs_dir, exist_ok=True)
    log_file = os.path.join(logs_dir, f"{room_id}.log")
    sname = _screen_name(room_id)

    game_args = [
        server_path,
        "-batchmode", "-nographics",
        "--server",
        "--port", str(port),
        "--room-id", room_id,
        "--max-players", str(max_players),
        "--server-secret", env_vars.get("GAME_SERVER_SECRET", ""),
        "--master-server-url", env_vars.get("MASTER_SERVER_URL", ""),
    ]

    # Redirect output to log file
    bash_cmd = f"{shlex.join(game_args)} >> {shlex.quote(log_file)} 2>&1"

    # Pass env vars to the screen session
    env = os.environ.copy()
    env.update(env_vars)

    cwd = server_dir if server_dir else os.path.dirname(server_path) or None

    try:
        subprocess.run(
            ["screen", "-dmS", sname, "bash", "-c", bash_cmd],
            cwd=cwd, env=env, check=True,
        )
        logger.info(f"Spawned screen session '{sname}' for room {room_id} on port {port}")
        return {"ok": True}
    except FileNotFoundError:
        return {"ok": False, "error": "screen not found — install with: apt install screen"}
    except subprocess.CalledProcessError as e:
        return {"ok": False, "error": str(e)}


def _kill(room_id: str) -> dict:
    sname = _screen_name(room_id)
    try:
        subprocess.run(["screen", "-S", sname, "-X", "quit"], check=True)
        logger.info(f"Killed screen session '{sname}'")
        return {"ok": True}
    except subprocess.CalledProcessError:
        # Session already dead — not an error
        logger.info(f"Screen session '{sname}' was already gone")
        return {"ok": True}


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        logger.debug(fmt, *args)

    def _send_json(self, code: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_GET(self):
        if self.path.startswith("/alive/"):
            room_id = self.path[len("/alive/"):]
            self._send_json(200, {"alive": _screen_is_alive(room_id)})
        elif self.path == "/health":
            self._send_json(200, {"status": "ok"})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/spawn":
            data = self._read_json()
            result = _spawn(
                room_id=data["room_id"],
                port=data["port"],
                max_players=data["max_players"],
                server_path=data["server_path"],
                server_dir=data.get("server_dir", ""),
                logs_dir=data.get("logs_dir", "/tmp/gameserver_logs"),
                env_vars=data.get("env_vars", {}),
            )
            self._send_json(200 if result["ok"] else 500, result)

        elif self.path.startswith("/kill/"):
            room_id = self.path[len("/kill/"):]
            self._send_json(200, _kill(room_id))

        else:
            self._send_json(404, {"error": "not found"})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    server = HTTPServer((BIND_HOST, BIND_PORT), _Handler)
    logger.info(f"Host agent listening on {BIND_HOST}:{BIND_PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")
