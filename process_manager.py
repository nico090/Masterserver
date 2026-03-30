import logging
import os
import shlex
import subprocess

import config

logger = logging.getLogger("master_server")

SCREEN_PREFIX = "gameserver_"


def _screen_name(room_id: str) -> str:
    return f"{SCREEN_PREFIX}{room_id}"


class ProcessManager:
    """Manages game-server lifecycle using screen sessions."""

    def __init__(self):
        self._known_rooms: set[str] = set()

    def spawn_server(self, room_id: str, port: int, max_players: int) -> bool:
        server_path = os.path.abspath(config.GAME_SERVER_PATH)
        if not os.path.isfile(server_path):
            logger.error(f"Binary not found: {server_path}")
            return False

        logs_dir = os.path.abspath(config.LOG_DIR)
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
            "--server-secret", config.SERVER_SECRET,
            "--master-server-url", config.MASTER_SERVER_INTERNAL_URL,
        ]

        bash_cmd = f"{shlex.join(game_args)} >> {shlex.quote(log_file)} 2>&1"
        cwd = config.GAME_SERVER_DIR or os.path.dirname(server_path) or None

        env = os.environ.copy()
        env["GAME_SERVER_SECRET"] = config.SERVER_SECRET
        env["MASTER_SERVER_URL"] = config.MASTER_SERVER_INTERNAL_URL

        try:
            subprocess.run(
                ["screen", "-dmS", sname, "bash", "-c", bash_cmd],
                cwd=cwd, env=env, check=True,
            )
            self._known_rooms.add(room_id)
            logger.info(f"Spawned server for room {room_id} on port {port}")
            return True
        except FileNotFoundError:
            logger.error("screen not found — install with: apt install screen")
            return False
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to spawn server for room {room_id}: {e}")
            return False

    def kill_server(self, room_id: str):
        self._known_rooms.discard(room_id)
        sname = _screen_name(room_id)
        try:
            subprocess.run(["screen", "-S", sname, "-X", "quit"], check=True)
            logger.info(f"Killed server for room {room_id}")
        except subprocess.CalledProcessError:
            logger.info(f"Screen session '{sname}' was already gone")

    def is_alive(self, room_id: str) -> bool:
        sname = _screen_name(room_id)
        result = subprocess.run(
            ["screen", "-ls", sname],
            capture_output=True, text=True,
        )
        return sname in result.stdout

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
