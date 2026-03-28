import json
import logging
import os
import urllib.error
import urllib.request

import config

logger = logging.getLogger("master_server")


class ProcessManager:
    """
    Delegates game-server lifecycle to host_agent.py running on the host.
    Communicates via HTTP so that processes spawn outside the Docker container.
    """

    def __init__(self):
        self._agent_url = config.HOST_AGENT_URL
        # Tracks room_ids this instance has spawned (lost on master restart, but
        # heartbeat timeouts handle cleanup of any restored rooms in that case).
        self._known_rooms: set[str] = set()

    # ------------------------------------------------------------------
    # Internal HTTP helpers (no external dependencies)
    # ------------------------------------------------------------------

    def _post(self, path: str, data: dict | None = None) -> dict:
        url = f"{self._agent_url}{path}"
        body = json.dumps(data).encode() if data else b""
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            payload = {}
            try:
                payload = json.loads(e.read())
            except Exception:
                pass
            return {"ok": False, "error": payload.get("error", str(e))}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _get(self, path: str) -> dict:
        url = f"{self._agent_url}{path}"
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                return json.loads(resp.read())
        except Exception as e:
            logger.warning(f"Host agent unreachable at {path}: {e}")
            return {}

    # ------------------------------------------------------------------
    # Public API (same interface as before)
    # ------------------------------------------------------------------

    def spawn_server(self, room_id: str, port: int, max_players: int) -> bool:
        payload = {
            "room_id": room_id,
            "port": port,
            "max_players": max_players,
            "server_path": os.path.abspath(config.GAME_SERVER_PATH),
            "server_dir": config.GAME_SERVER_DIR or "",
            "logs_dir": os.path.abspath(config.LOG_DIR),
            "env_vars": {
                "GAME_SERVER_SECRET": config.SERVER_SECRET,
                "MASTER_SERVER_URL": config.MASTER_SERVER_INTERNAL_URL,
            },
        }
        result = self._post("/spawn", payload)
        if result.get("ok"):
            self._known_rooms.add(room_id)
            logger.info(f"Spawned server for room {room_id} on port {port} (screen on host)")
            return True
        logger.error(f"Failed to spawn server for room {room_id}: {result.get('error')}")
        return False

    def kill_server(self, room_id: str):
        self._known_rooms.discard(room_id)
        result = self._post(f"/kill/{room_id}")
        if not result.get("ok"):
            logger.warning(f"Kill server {room_id}: {result.get('error')}")
        else:
            logger.info(f"Killed server for room {room_id}")

    def is_alive(self, room_id: str) -> bool:
        result = self._get(f"/alive/{room_id}")
        return result.get("alive", False)

    def cleanup_dead(self) -> list[str]:
        """Returns room_ids whose screen sessions have exited."""
        dead = []
        for room_id in list(self._known_rooms):
            if not self.is_alive(room_id):
                dead.append(room_id)
                self._known_rooms.discard(room_id)
        return dead

    def kill_all(self):
        for room_id in list(self._known_rooms):
            self.kill_server(room_id)
