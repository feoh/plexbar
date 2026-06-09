"""Playback queue and mpv subprocess integration."""

import shutil
import subprocess
from dataclasses import dataclass, field

from plexbar.models import QueueTrack


class MpvNotFoundError(RuntimeError):
    """Raised when mpv is required but not installed."""


@dataclass
class PlaybackQueue:
    """In-memory playback queue."""

    tracks: list[QueueTrack] = field(default_factory=list)
    current_index: int = -1

    @property
    def current(self) -> QueueTrack | None:
        """Return the current track, if any."""

        if 0 <= self.current_index < len(self.tracks):
            return self.tracks[self.current_index]
        return None

    def append(self, tracks: list[QueueTrack]) -> None:
        """Append tracks to the queue."""

        first_new_index = len(self.tracks)
        self.tracks.extend(tracks)
        if self.current_index == -1 and tracks:
            self.current_index = first_new_index

    def replace(self, tracks: list[QueueTrack]) -> QueueTrack | None:
        """Replace the queue and return the first track."""

        self.tracks = list(tracks)
        self.current_index = 0 if self.tracks else -1
        return self.current

    def next(self) -> QueueTrack | None:
        """Advance to the next track and return it."""

        if self.current_index + 1 < len(self.tracks):
            self.current_index += 1
            return self.current
        self.current_index = -1
        return None

    def labels(self) -> list[str]:
        """Return display labels for the queue."""

        labels: list[str] = []
        for index, track in enumerate(self.tracks):
            prefix = "▶ " if index == self.current_index else "  "
            labels.append(f"{prefix}{track.label}")
        return labels


class MpvPlayer:
    """Simple one-track-at-a-time mpv controller."""

    def __init__(self) -> None:
        if shutil.which("mpv") is None:
            raise MpvNotFoundError(
                "mpv was not found on PATH; install mpv to use Plexbar playback."
            )
        self._process: subprocess.Popen[str] | None = None
        self._paused = False

    @property
    def is_running(self) -> bool:
        """Return whether mpv is currently running."""

        return self._process is not None and self._process.poll() is None

    def play(self, track: QueueTrack) -> None:
        """Start playing a track, replacing any existing mpv process."""

        self.stop()
        self._paused = False
        self._process = subprocess.Popen(  # noqa: S603
            ["mpv", "--no-video", "--really-quiet", track.stream_url],
            stdin=subprocess.PIPE,
            text=True,
        )

    def pause_resume(self) -> None:
        """Toggle pause/resume in mpv."""

        if (
            self._process is None
            or self._process.stdin is None
            or self._process.poll() is not None
        ):
            return
        self._process.stdin.write("p\n")
        self._process.stdin.flush()
        self._paused = not self._paused

    def reap_finished(self) -> bool:
        """Clear and report a naturally finished mpv process."""

        if self._process is None or self._process.poll() is None:
            return False
        self._process = None
        self._paused = False
        return True

    def stop(self) -> None:
        """Stop mpv if it is running."""

        if self._process is None:
            return
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None
        self._paused = False
