"""Playback queue and mpv subprocess integration."""

import json
import shutil
import socket
import subprocess
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from plexbar.models import QueueTrack


class MpvNotFoundError(RuntimeError):
    """Raised when mpv is required but not installed."""


@dataclass
class PlaybackQueue:
    """In-memory playback queue."""

    tracks: list[QueueTrack] = field(default_factory=list)
    current_index: int = -1
    next_index: int = 0

    @property
    def current(self) -> QueueTrack | None:
        """Return the track currently being played, if any."""

        if 0 <= self.current_index < len(self.tracks):
            return self.tracks[self.current_index]
        return None

    def append(self, tracks: list[QueueTrack]) -> None:
        """Append tracks to the queue without changing playback state."""

        self.tracks.extend(tracks)

    def replace(self, tracks: list[QueueTrack]) -> QueueTrack | None:
        """Replace the queue and return the first track to play."""

        self.tracks = list(tracks)
        self.current_index = 0 if self.tracks else -1
        self.next_index = 1 if self.tracks else 0
        return self.current

    def next(self) -> QueueTrack | None:
        """Advance to the next unplayed track and return it."""

        if self.next_index < len(self.tracks):
            self.current_index = self.next_index
            self.next_index += 1
            return self.current
        self.current_index = -1
        return None

    def clear_current(self) -> None:
        """Mark that no queued track is currently being played."""

        self.current_index = -1

    def labels(self) -> list[str]:
        """Return display labels for the queue."""

        labels: list[str] = []
        for index, track in enumerate(self.tracks):
            prefix = "▶ " if index == self.current_index else "  "
            labels.append(f"{prefix}{track.label}")
        return labels


class MpvPlayer:
    """Persistent mpv controller using JSON IPC."""

    def __init__(self) -> None:
        if shutil.which("mpv") is None:
            raise MpvNotFoundError(
                "mpv was not found on PATH; install mpv to use Plexbar playback."
            )
        self._process: subprocess.Popen[bytes] | None = None
        self._socket_dir = tempfile.TemporaryDirectory(prefix="plexbar-mpv-")
        self._socket_path = Path(self._socket_dir.name) / "mpv.sock"
        self._paused = False
        self._loaded = False
        self._request_id = 0
        self._start_mpv()

    def __del__(self) -> None:
        """Best-effort cleanup if a caller forgets to close the player."""

        with suppress(Exception):
            self.close()

    @property
    def is_running(self) -> bool:
        """Return whether mpv is currently running."""

        return self._process is not None and self._process.poll() is None

    def play(self, track: QueueTrack) -> None:
        """Start playing a track, replacing any currently loaded track."""

        self._ensure_running()
        self._command(["loadfile", track.stream_url, "replace"])
        self._paused = False
        self._loaded = True

    def pause_resume(self) -> None:
        """Toggle pause/resume in mpv."""

        if not self._loaded:
            return
        self._ensure_running()
        self._command(["cycle", "pause"])
        self._paused = not self._paused

    def reap_finished(self) -> bool:
        """Report whether the current track finished and mpv returned to idle."""

        if not self._loaded:
            return False
        if not self.is_running:
            self._loaded = False
            self._paused = False
            return True
        idle_active = self._get_property("idle-active")
        if not idle_active:
            return False
        self._loaded = False
        self._paused = False
        return True

    def stop(self) -> None:
        """Stop the currently loaded track while keeping mpv ready for commands."""

        if self.is_running and self._loaded:
            self._command(["stop"])
        self._loaded = False
        self._paused = False

    def close(self) -> None:
        """Terminate mpv and clean up the IPC socket."""

        if self._process is not None and self._process.poll() is None:
            try:
                self._command(["quit"])
                self._process.wait(timeout=2)
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                self._process.terminate()
                try:
                    self._process.wait(timeout=2)
                except subprocess.TimeoutExpired as _exc:
                    self._process.kill()
        self._process = None
        self._loaded = False
        self._paused = False
        self._socket_dir.cleanup()

    def _start_mpv(self) -> None:
        if self._socket_path.exists():
            self._socket_path.unlink()
        self._process = subprocess.Popen(  # noqa: S603
            [
                "mpv",
                "--idle=yes",
                "--no-video",
                "--really-quiet",
                f"--input-ipc-server={self._socket_path}",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._wait_for_socket()

    def _ensure_running(self) -> None:
        if self.is_running:
            return
        self._start_mpv()

    def _wait_for_socket(self) -> None:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if self._socket_path.exists():
                return
            if self._process is not None and self._process.poll() is not None:
                msg = "mpv exited before its IPC socket became available."
                raise RuntimeError(msg)
            time.sleep(0.01)
        msg = "Timed out waiting for mpv IPC socket."
        raise RuntimeError(msg)

    def _command(self, command: list[object]) -> dict[str, Any]:
        return self._request({"command": command})

    def _get_property(self, name: str) -> Any:
        return self._command(["get_property", name]).get("data")

    def _request(self, payload: dict[str, object]) -> dict[str, Any]:
        self._request_id += 1
        request_id = self._request_id
        request_payload = {**payload, "request_id": request_id}
        request = json.dumps(request_payload).encode("utf-8") + b"\n"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(0.5)
            client.connect(str(self._socket_path))
            client.sendall(request)
            response = self._read_response(client, request_id)
        if response.get("error") not in {None, "success"}:
            msg = f"mpv command failed: {response['error']}"
            raise RuntimeError(msg)
        return response

    def _read_response(
        self, client: socket.socket, expected_request_id: int
    ) -> dict[str, Any]:
        buffer = b""
        while True:
            chunk = client.recv(65536)
            if not chunk:
                msg = "mpv closed its IPC socket before responding."
                raise RuntimeError(msg)
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if not line:
                    continue
                try:
                    data = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError as exc:
                    msg = "mpv returned invalid JSON over IPC."
                    raise RuntimeError(msg) from exc
                if not isinstance(data, dict):
                    msg = "mpv returned an invalid IPC response."
                    raise RuntimeError(msg)
                if data.get("request_id") == expected_request_id:
                    return data
