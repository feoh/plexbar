"""Plex API wrapper for music browsing."""

import math
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, cast

from plexapi.audio import Track  # type: ignore[import-untyped]
from plexapi.library import MusicSection  # type: ignore[import-untyped]
from plexapi.server import PlexServer  # type: ignore[import-untyped]

from plexbar.models import BrowserItem, ItemKind, QueueTrack

# Plex metadata (often tagged from MusicBrainz) uses typographic punctuation,
# e.g. "Yo‐Yo Ma" with U+2010 HYPHEN, which never matches what users type.
# Dashes fold to spaces so "Yo-Yo Ma", "yo yo ma", and "Yo‐Yo Ma" all agree.
# Letters listed here have no Unicode decomposition, so stripping accents in
# ``_search_key`` would leave them alone.
_SEARCH_FOLDS = str.maketrans(
    {
        **dict.fromkeys("-\u2010\u2011\u2012\u2013\u2014\u2015\u2212", " "),
        **dict.fromkeys("\u2018\u2019\u02bc", "'"),
        **dict.fromkeys("\u201c\u201d", '"'),
        **dict.fromkeys("øØ", "o"),
        **dict.fromkeys("łŁ", "l"),
        **dict.fromkeys("đĐ", "d"),
        **dict.fromkeys("æÆ", "ae"),
        **dict.fromkeys("œŒ", "oe"),
    }
)


@dataclass(frozen=True)
class GenreArtist:
    """Artist identity discovered from genre-matched tracks."""

    title: str
    rating_key: str = ""


class PlexMusicClient:
    """Small adapter around python-plexapi for the UI."""

    # Every artist and album, kept for local search matching. Small enough to
    # fetch whole; refreshed whenever the full Artists or Albums list is browsed.
    _artist_cache: list[Any] | None = None
    _album_cache: list[Any] | None = None

    def __init__(self, config: Any) -> None:
        self.config = config
        self.server = PlexServer(config.base_url, config.token)
        self.library = self._default_music_library(config.default_library)

    @staticmethod
    def validate(base_url: str, token: str) -> list[str]:
        """Validate Plex credentials and return available music library names."""

        server = PlexServer(base_url, token)
        return [section.title for section in music_sections(server)]

    def root_items(self) -> list[BrowserItem]:
        """Top-level browser sections."""

        return [
            BrowserItem("Artists", ItemKind.ARTISTS),
            BrowserItem("Albums", ItemKind.ALBUMS),
            BrowserItem("Tracks", ItemKind.TRACKS),
            BrowserItem("Playlists", ItemKind.PLAYLISTS),
            BrowserItem("Genres", ItemKind.GENRES),
        ]

    def artist_browse_items(self) -> list[BrowserItem]:
        """Return artist rows prefixed with recently added."""

        return [
            BrowserItem("Recently Added", ItemKind.ARTISTS_RECENTLY_ADDED),
            *self.artists(),
        ]

    def album_browse_items(self) -> list[BrowserItem]:
        """Return album rows prefixed with recently added."""

        return [
            BrowserItem("Recently Added", ItemKind.ALBUMS_RECENTLY_ADDED),
            *self.albums(),
        ]

    def artists(self) -> list[BrowserItem]:
        """Return artist rows."""

        artists = self._artist_cache = list(self.library.search(libtype="artist"))
        return [self._artist_item(artist) for artist in artists]

    def recently_added_artists(self) -> list[BrowserItem]:
        """Return recently added artist rows."""

        artists = self.library.recentlyAdded(libtype="artist")
        return [self._artist_item(artist) for artist in artists]

    def albums(self, artist: Any | None = None) -> list[BrowserItem]:
        """Return album rows, optionally scoped to an artist."""

        if artist is not None:
            return self._album_items(artist.albums())
        albums = self._album_cache = list(self.library.albums())
        return self._album_items(albums)

    def recently_added_albums(self) -> list[BrowserItem]:
        """Return recently added album rows."""

        albums = self.library.recentlyAdded(libtype="album")
        return self._album_items(albums)

    def tracks(self, parent: Any | None = None) -> list[BrowserItem]:
        """Return track rows, optionally scoped to an album or playlist."""

        if parent is None:
            tracks = self.library.search(libtype="track")
        elif hasattr(parent, "tracks"):
            tracks = parent.tracks()
        elif hasattr(parent, "items"):
            tracks = [item for item in parent.items() if _is_track(item)]
        else:
            tracks = []
        return [self.track_item(track) for track in tracks]

    def genre_browse_items(self, genre: Any) -> list[BrowserItem]:
        """Return the browse modes available for a genre."""

        return [
            BrowserItem("By Artist", ItemKind.GENRE_ARTISTS, genre),
            BrowserItem("By Album", ItemKind.GENRE_ALBUMS, genre),
        ]

    def artists_for_genre(self, genre: Any) -> list[BrowserItem]:
        """Return artist rows with tracks in a genre."""

        artists_by_key: dict[str, GenreArtist] = {}
        for track in self._genre_tracks(genre):
            artist = _genre_artist_for_track(track)
            if artist is not None:
                artists_by_key.setdefault(_genre_artist_key(artist), artist)
        return [
            BrowserItem(artist.title, ItemKind.GENRE_ARTIST, (genre, artist))
            for artist in sorted(
                artists_by_key.values(), key=lambda item: item.title.casefold()
            )
        ]

    def albums_for_genre(
        self, genre: Any, artist: Any | None = None
    ) -> list[BrowserItem]:
        """Return album rows with tracks in a genre, optionally scoped to an artist."""

        albums = []
        for track in self._genre_tracks(genre):
            if artist is None or _same_artist(track, artist):
                album = track.album()
                if album is not None:
                    albums.append(album)
        return [
            BrowserItem(
                str(album.title),
                ItemKind.GENRE_ALBUM,
                (genre, album),
                _safe_title(album, "parentTitle"),
            )
            for album in sorted(
                _unique_by_key(albums),
                key=lambda item: (
                    _safe_title(item, "parentTitle").casefold(),
                    _safe_title(item, "title").casefold(),
                ),
            )
        ]

    def tracks_for_genre_album(self, _genre: Any, album: Any) -> list[BrowserItem]:
        """Return all track rows for an album found through genre browsing."""

        return self.tracks(album)

    def playlists(self) -> list[BrowserItem]:
        """Return music playlists.

        Filter by Plex's ``playlistType`` on the server rather than fetching
        every playlist's items to inspect them, which costs one request per
        playlist.
        """

        return [
            BrowserItem(str(playlist.title), ItemKind.PLAYLIST, playlist)
            for playlist in self.server.playlists(playlistType="audio")
            if playlist is not None
        ]

    def genres(self) -> list[BrowserItem]:
        """Return available track genres."""

        genres = self.library.listFilterChoices("genre", libtype="track")
        items: list[BrowserItem] = []
        for genre in genres:
            title = getattr(genre, "title", None)
            if title:
                items.append(BrowserItem(str(title), ItemKind.GENRE, genre))
        return items

    def search(self, query: str) -> list[BrowserItem]:
        """Search the configured music library by title.

        Artists and albums are matched locally against cached lists so that
        punctuation, accent, and case differences (see ``_search_key``) don't hide
        results. Tracks are too numerous to cache and are searched on the Plex
        server by title.
        """

        key = _search_key(query)
        items = [
            self._artist_item(artist)
            for artist in self._search_artists()
            if key in _search_key(_safe_title(artist, "title"))
        ]
        items.extend(
            self._album_items(
                album
                for album in self._search_albums()
                if key in _search_key(_safe_title(album, "title"))
            )
        )
        # PlexAPI's ``MusicSection.search`` does not accept a ``query`` keyword;
        # unknown keywords are treated as filter fields, which can raise for
        # music libraries. Filter by title instead.
        items.extend(
            self.track_item(track)
            for track in self.library.search(title=query, libtype="track")
            if _is_track(track)
        )
        items.extend(
            playlist
            for playlist in self.playlists()
            if key in _search_key(playlist.title)
        )
        return items

    def warm_search_cache(self) -> None:
        """Fetch the artist and album lists so the first search is fast."""

        self._search_artists()
        self._search_albums()

    def _search_artists(self) -> list[Any]:
        if self._artist_cache is None:
            self._artist_cache = list(self.library.search(libtype="artist"))
        return self._artist_cache

    def _search_albums(self) -> list[Any]:
        if self._album_cache is None:
            self._album_cache = list(self.library.albums())
        return self._album_cache

    def playable_tracks(self, item: BrowserItem) -> list[QueueTrack]:
        """Expand a browser item to queueable tracks."""

        match item.kind:
            case ItemKind.TRACK if item.source is not None:
                return [self.queue_track(item.source)]
            case ItemKind.ALBUM if item.source is not None:
                return [self.queue_track(track) for track in item.source.tracks()]
            case ItemKind.PLAYLIST if item.source is not None:
                return [
                    self.queue_track(track)
                    for track in self._playlist_tracks(item.source)
                ]
            case ItemKind.GENRE_ARTIST if item.source is not None:
                genre, artist = _genre_scoped_source(item.source)
                return [
                    self.queue_track(track)
                    for album in self.albums_for_genre(genre, artist)
                    if album.source is not None
                    for track in _genre_scoped_source(album.source)[1].tracks()
                ]
            case ItemKind.GENRE_ALBUM if item.source is not None:
                _genre, album = _genre_scoped_source(item.source)
                return [self.queue_track(track) for track in album.tracks()]
            case ItemKind.ARTIST if item.source is not None:
                return [
                    self.queue_track(track)
                    for album in item.source.albums()
                    for track in album.tracks()
                ]
            case _:
                return []

    def track_item(self, track: Track) -> BrowserItem:
        """Convert a Plex track to a browser row."""

        return BrowserItem(track.title, ItemKind.TRACK, track, _track_subtitle(track))

    def _artist_item(self, artist: Any) -> BrowserItem:
        """Convert a Plex artist to a browser row."""

        return BrowserItem(str(artist.title), ItemKind.ARTIST, artist)

    def _album_items(self, albums: Iterable[Any]) -> list[BrowserItem]:
        """Convert Plex albums to browser rows."""

        return [
            BrowserItem(
                str(album.title),
                ItemKind.ALBUM,
                album,
                _safe_title(album, "parentTitle"),
            )
            for album in albums
            if album is not None
        ]

    def queue_track(self, track: Track) -> QueueTrack:
        """Convert a Plex track to a playback queue item."""

        return QueueTrack(
            title=track.title,
            artist=_safe_title(track, "grandparentTitle"),
            album=_safe_title(track, "parentTitle"),
            stream_url=track.getStreamURL(),
            artwork_url=_artwork_url(track),
            duration_seconds=_duration_seconds(track),
        )

    def _default_music_library(self, preferred_name: str | None) -> MusicSection:
        sections = music_sections(self.server)
        if not sections:
            msg = "No Plex music libraries were found."
            raise RuntimeError(msg)
        if preferred_name:
            for section in sections:
                if section.title == preferred_name:
                    return section
        return sections[0]

    def _playlist_tracks(self, playlist: Any) -> list[Track]:
        return [cast(Track, item) for item in playlist.items() if _is_track(item)]

    def _genre_tracks(self, genre: Any) -> list[Track]:
        return [
            cast(Track, item)
            for item in self.library.search(libtype="track", genre=genre)
            if _is_track(item)
        ]


def music_sections(server: PlexServer) -> list[MusicSection]:
    """Return only music sections from a Plex server."""

    return [
        section
        for section in server.library.sections()
        if isinstance(section, MusicSection)
    ]


def _track_subtitle(track: Track) -> str:
    artist = _safe_title(track, "grandparentTitle")
    album = _safe_title(track, "parentTitle")
    return " — ".join(part for part in [artist, album] if part)


def _genre_scoped_source(source: Any) -> tuple[Any, Any]:
    genre, scoped_item = source
    return genre, scoped_item


def _genre_artist_for_track(track: Track) -> GenreArtist | None:
    title = _safe_title(track, "grandparentTitle")
    if not title:
        artist = track.artist()
        if artist is None:
            return None
        title = _safe_title(artist, "title")
        rating_key = _safe_title(artist, "ratingKey")
    else:
        rating_key = _safe_title(track, "grandparentRatingKey")
    if not title:
        return None
    return GenreArtist(title=title, rating_key=rating_key)


def _genre_artist_key(artist: GenreArtist) -> str:
    return artist.rating_key or artist.title.casefold()


def _unique_by_key(items: Iterable[Any]) -> list[Any]:
    seen: set[str] = set()
    unique_items: list[Any] = []
    for item in items:
        key = _metadata_key(item)
        if key not in seen:
            seen.add(key)
            unique_items.append(item)
    return unique_items


def _metadata_key(item: Any) -> str:
    rating_key = getattr(item, "ratingKey", None)
    if rating_key is not None:
        return str(rating_key)
    return f"{getattr(item, 'TYPE', '')}:{getattr(item, 'title', id(item))}"


def _same_artist(track: Track, artist: Any) -> bool:
    if isinstance(artist, GenreArtist):
        track_artist = _genre_artist_for_track(track)
        return track_artist is not None and _genre_artist_key(
            track_artist
        ) == _genre_artist_key(artist)
    artist_key = getattr(artist, "ratingKey", None)
    if artist_key is not None:
        return str(getattr(track, "grandparentRatingKey", "")) == str(artist_key)
    return _safe_title(track, "grandparentTitle") == _safe_title(artist, "title")


def _same_album(track: Track, album: Any) -> bool:
    album_key = getattr(album, "ratingKey", None)
    if album_key is not None:
        return str(getattr(track, "parentRatingKey", "")) == str(album_key)
    return _safe_title(track, "parentTitle") == _safe_title(album, "title")


def _safe_title(item: Any, attr: str) -> str:
    return str(getattr(item, attr, "") or "")


def _search_key(text: str) -> str:
    """Normalize text for punctuation-, accent-, and case-insensitive matching."""

    # NFKD splits accented letters into base letter + combining mark ("ö" ->
    # "o" + U+0308); dropping the marks leaves the unaccented letter.
    decomposed = unicodedata.normalize("NFKD", text.translate(_SEARCH_FOLDS))
    unaccented = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(unaccented.casefold().split())


def _duration_seconds(track: Track) -> float:
    """Convert Plex's millisecond track duration to seconds."""

    try:
        duration = float(getattr(track, "duration", 0) or 0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(duration):
        return 0.0
    return max(duration, 0.0) / 1000


def _artwork_url(track: Track) -> str:
    for attr in ("squareArtUrl", "thumbUrl", "artUrl"):
        try:
            url = getattr(track, attr, None)
        except Exception:  # noqa: BLE001 - artwork is optional metadata
            continue
        if url:
            return str(url)
    return ""


def _is_track(item: Any) -> bool:
    return isinstance(item, Track) or getattr(item, "TYPE", None) == "track"
