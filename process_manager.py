import logging
import os
import subprocess
import sys

import config

logger = logging.getLogger("master_server")

IS_WINDOWS = sys.platform == "win32"


class ProcessManager:
    def __init__(self):
        self._processes: dict[str, subprocess.Popen] = {}
        self._log_files: dict[str, list] = {}  # room_id -> [stdout_file, stderr_file]

    def spawn_server(self, room_id: str, port: int, max_players: int) -> bool:
        server_path = os.path.abspath(config.GAME_SERVER_PATH)
        if not os.path.isfile(server_path):
            logger.error(f"Game server binary not found at: {server_path}")
            return False

        # Pass the secret via environment variable instead of command line
        env = os.environ.copy()
        env["GAME_SERVER_SECRET"] = config.SERVER_SECRET
        env["MASTER_SERVER_URL"] = config.MASTER_SERVER_INTERNAL_URL

        cmd = [
            server_path,
            "-batchmode",
            "-nographics",
            "--server",
            "--port", str(port),
            "--room-id", room_id,
            "--max-players", str(max_players),
            "--server-secret", config.SERVER_SECRET,
            "--master-server-url", config.MASTER_SERVER_INTERNAL_URL,
        ]

        # Working directory for the server process
        cwd = config.GAME_SERVER_DIR or os.path.dirname(server_path) or None

        # Always log to files for debugging
        logs_dir = os.path.abspath(config.LOG_DIR)
        os.makedirs(logs_dir, exist_ok=True)
        stdout_file = open(os.path.join(logs_dir, f"{room_id}_stdout.log"), "w")
        stderr_file = open(os.path.join(logs_dir, f"{room_id}_stderr.log"), "w")

        try:
            kwargs = {}
            if IS_WINDOWS:
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

            proc = subprocess.Popen(
                cmd, stdout=stdout_file, stderr=stderr_file,
                cwd=cwd, env=env, **kwargs
            )
            self._processes[room_id] = proc
            self._log_files[room_id] = [stdout_file, stderr_file]
            logger.info(f"Spawned server for room {room_id} on port {port}, pid={proc.pid}")
            return True
        except Exception as e:
            logger.error(f"Failed to spawn server for room {room_id}: {e}")
            stdout_file.close()
            stderr_file.close()
            return False

    def kill_server(self, room_id: str):
        proc = self._processes.pop(room_id, None)
        if proc is None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            logger.info(f"Killed server for room {room_id}, pid={proc.pid}")
        except Exception as e:
            logger.error(f"Error killing server for room {room_id}: {e}")
        finally:
            self._close_log_files(room_id)

    def is_alive(self, room_id: str) -> bool:
        proc = self._processes.get(room_id)
        if proc is None:
            return False
        return proc.poll() is None

    def cleanup_dead(self) -> list[str]:
        """Returns room_ids whose processes have exited."""
        dead = []
        for room_id, proc in list(self._processes.items()):
            if proc.poll() is not None:
                dead.append(room_id)
                self._processes.pop(room_id, None)
                self._close_log_files(room_id)
        return dead

    def kill_all(self):
        for room_id in list(self._processes.keys()):
            self.kill_server(room_id)

    def _close_log_files(self, room_id: str):
        files = self._log_files.pop(room_id, None)
        if files:
            for f in files:
                try:
                    f.close()
                except Exception:
                    pass
