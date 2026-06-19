"""Playify TUI — Bot subprocess management with log capture."""

import subprocess
import sys
import os
import threading
import time
import signal
from pathlib import Path
from collections import deque
from typing import Optional


class BotProcess:
    """Manages the Playify bot as a subprocess with real-time log capture."""

    def __init__(self, project_root: Path, python_exe: str | None = None):
        self.project_root = project_root
        self.python_exe = python_exe or sys.executable
        self.process: Optional[subprocess.Popen] = None
        self.log_lines: deque[str] = deque(maxlen=500)
        self._reader_thread: Optional[threading.Thread] = None
        self._running = False
        self.started_at: Optional[float] = None
        self.crash_count = 0
        self.last_exit_code: Optional[int] = None

        # Parsed info from logs
        self.bot_name: str = "Playify"
        self.is_online: bool = False
        self.guild_count: int = 0
        self.synced_commands: int = 0
        self.current_song: str = ""
        self.current_artist: str = ""
        
        self.active_players: int = 0
        self.queued_songs: int = 0
        self.url_cache_size: int = 0

    @property
    def is_running(self) -> bool:
        """Check if the bot process is currently running."""
        if self.process is None:
            return False
        return self.process.poll() is None

    @property
    def uptime_seconds(self) -> float:
        """Get the uptime in seconds."""
        if self.started_at and self.is_running:
            return time.time() - self.started_at
        return 0.0

    @property
    def uptime_str(self) -> str:
        """Get a formatted uptime string."""
        secs = int(self.uptime_seconds)
        if secs < 60:
            return f"{secs}s"
        elif secs < 3600:
            return f"{secs // 60}m {secs % 60}s"
        else:
            h = secs // 3600
            m = (secs % 3600) // 60
            return f"{h}h {m}m"

    @property
    def memory_mb(self) -> float:
        """Get the memory usage of the bot process in MB."""
        if not self.is_running or self.process is None:
            return 0.0
        try:
            import psutil
            parent = psutil.Process(self.process.pid)
            total_rss = parent.memory_info().rss
            for child in parent.children(recursive=True):
                try:
                    total_rss += child.memory_info().rss
                except psutil.NoSuchProcess:
                    pass
            return total_rss / (1024 * 1024)
        except Exception:
            return 0.0

    def start(self) -> None:
        """Start the bot subprocess."""
        if self.is_running:
            return

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        # Ensure bin/ is on PATH for ffmpeg
        bin_dir = str(self.project_root / "bin")
        env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")

        self.process = subprocess.Popen(
            [self.python_exe, str(self.project_root / "playify.py")],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(self.project_root),
            env=env,
            bufsize=1,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )

        self._running = True
        self.started_at = time.time()
        self.is_online = False

        self._reader_thread = threading.Thread(target=self._read_output, daemon=True)
        self._reader_thread.start()

    def stop(self) -> None:
        """Gracefully stop the bot subprocess."""
        self._running = False
        if self.process and self.is_running:
            try:
                if os.name == "nt":
                    # On Windows, send CTRL_BREAK_EVENT for graceful shutdown
                    self.process.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    self.process.terminate()

                # Wait up to 10 seconds for graceful shutdown
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass

        self.last_exit_code = self.process.returncode if self.process else None
        self.is_online = False

    def restart(self) -> None:
        """Stop and restart the bot."""
        self.stop()
        time.sleep(1)
        self.start()

    def _read_output(self) -> None:
        """Background thread that reads stdout/stderr from the bot process."""
        if not self.process or not self.process.stdout:
            return

        try:
            for line in self.process.stdout:
                if not self._running:
                    break

                line = line.rstrip("\n\r")
                if line:
                    if "[stats]" not in line.lower():
                        self.log_lines.append(line)
                    self._parse_log_line(line)
        except Exception:
            pass
        finally:
            if self.process:
                self.last_exit_code = self.process.poll()
                if self.last_exit_code is not None and self.last_exit_code != 0:
                    self.crash_count += 1
            self.is_online = False

    def _parse_log_line(self, line: str) -> None:
        """Parse a log line to extract useful bot state information."""
        lower = line.lower()

        # Detect "bot is online"
        if "is online" in lower or "logged in as" in lower:
            self.is_online = True
            # Try to extract bot name
            if "logged in as" in lower:
                parts = line.split("logged in as")
                if len(parts) > 1:
                    self.bot_name = parts[1].strip().split()[0]

        # Detect synced commands
        if "synced" in lower and "slash commands" in lower:
            import re
            match = re.search(r"synced\s+(\d+)", lower)
            if match:
                self.synced_commands = int(match.group(1))

        # Detect guild count (from presence rotation logs or similar)
        if "servers" in lower or "guilds" in lower:
            import re
            match = re.search(r"(\d+)\s*(?:server|guild)", lower)
            if match:
                self.guild_count = int(match.group(1))

        # Detect current playing song
        if "playing" in lower and "[" in lower:
            import re
            match = re.search(r"playing\s+['\"](.+?)['\"]", line, re.IGNORECASE)
            if match:
                self.current_song = match.group(1)

        # Detect playback stopped
        if "stopping playback" in lower or "manual stop" in lower or "voice client disconnected" in lower:
            self.current_song = ""
            self.current_artist = ""

        # Detect disconnect / offline
        if "disconnected" in lower or "closed" in lower:
            if "guild" not in lower:  # Don't trigger on guild disconnects
                self.is_online = False
                self.current_song = ""
                
        # Detect custom [STATS] log
        if "[stats]" in lower:
            import re
            m_servers = re.search(r"servers:\s*(\d+)", lower)
            if m_servers: self.guild_count = int(m_servers.group(1))
            
            m_players = re.search(r"players:\s*(\d+)", lower)
            if m_players: self.active_players = int(m_players.group(1))
            
            m_queued = re.search(r"queued:\s*(\d+)", lower)
            if m_queued: self.queued_songs = int(m_queued.group(1))
            
            m_cache = re.search(r"cache:\s*(\d+)", lower)
            if m_cache: self.url_cache_size = int(m_cache.group(1))

    @property
    def ffmpeg_processes(self) -> int:
        """Count active ffmpeg processes spawned by this bot."""
        if not self.is_running or self.process is None:
            return 0
        try:
            import psutil
            parent = psutil.Process(self.process.pid)
            count = 0
            for child in parent.children(recursive=True):
                try:
                    if "ffmpeg" in child.name().lower():
                        count += 1
                except psutil.NoSuchProcess:
                    pass
            return count
        except Exception:
            return 0

    def get_recent_logs(self, count: int = 20) -> list[str]:
        """Get the most recent log lines."""
        logs = list(self.log_lines)
        return logs[-count:] if len(logs) > count else logs

    def get_all_logs(self) -> list[str]:
        """Get all captured log lines."""
        return list(self.log_lines)
