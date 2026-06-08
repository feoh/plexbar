"""Small domain models used by the Plexbar UI."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ItemKind(StrEnum):
    """Kinds of Plex music items Plexbar can browse."""

    ROOT = "root"
    ARTISTS = "artists"
    ALBUMS = "albums"
    TRACKS = "tracks"
    PLAYLISTS = "playlists"
    ARTIST = "artist"
    ALBUM = "album"
    TRACK = "track"
    PLAYLIST = "playlist"
    BACK = "back"


@dataclass(frozen=True)
class BrowserItem:
    """A row displayed in the browser list."""

    title: str
    kind: ItemKind
    source: Any | None = None
    subtitle: str = ""

    @property
    def display_title(self) -> str:
        """Human-readable title with optional subtitle."""

        if self.subtitle:
            return f"{self.title} — {self.subtitle}"
        return self.title


@dataclass(frozen=True)
class QueueTrack:
    """A track queued for playback."""

    title: str
    artist: str
    album: str
    stream_url: str

    @property
    def label(self) -> str:
        """Human-readable track label."""

        bits = [self.title]
        if self.artist:
            bits.append(self.artist)
        if self.album:
            bits.append(self.album)
        return " — ".join(bits)
