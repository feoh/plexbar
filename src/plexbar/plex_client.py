"""Plex API wrapper for music browsing."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, cast

from plexapi.audio import Album, Artist, Track  # type: ignore[import-untyped]
from plexapi.library import MusicSection  # type: ignore[import-untyped]
from plexapi.server import PlexServer  # type: ignore[import-untyped]

from plexbar.models import BrowserItem, ItemKind, QueueTrack


@dataclass(frozen=True)
class GenreArtist:
    """Artist identity discovered from genre-matched tracks."""

    title: str
    rating_key: str = ""


class PlexMusicClient:
    """Small adapter around python-plexapi for the UI."""

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

    def artists(self) -> list[BrowserItem]:
        """Return artist rows."""

        artists = self.library.search(libtype="artist")
        return [BrowserItem(item.title, ItemKind.ARTIST, item) for item in artists]

    def albums(self, artist: Any | None = None) -> list[BrowserItem]:
        """Return album rows, optionally scoped to an artist."""

        albums = artist.albums() if artist is not None else self.library.albums()
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
        """Return music playlists."""

        playlists = [
            playlist
            for playlist in self.server.playlists()
            if playlist is not None and _is_audio_playlist(playlist)
        ]
        return [
            BrowserItem(str(playlist.title), ItemKind.PLAYLIST, playlist)
            for playlist in playlists
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

        PlexAPI's ``MusicSection.search`` does not accept a ``query`` keyword;
        unknown keywords are treated as filter fields, which can raise for music
        libraries. Search each supported music type by title instead.
        """

        items: list[BrowserItem] = []
        for libtype in ("artist", "album", "track"):
            results = self.library.search(title=query, libtype=libtype)
            for result in results:
                item = self._item_from_result(result)
                if item is not None:
                    items.append(item)
        items.extend(
            playlist
            for playlist in self.playlists()
            if query.casefold() in playlist.title.casefold()
        )
        return items

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

    def queue_track(self, track: Track) -> QueueTrack:
        """Convert a Plex track to a playback queue item."""

        return QueueTrack(
            title=track.title,
            artist=_safe_title(track, "grandparentTitle"),
            album=_safe_title(track, "parentTitle"),
            stream_url=track.getStreamURL(),
            artwork_url=_artwork_url(track),
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

    def _item_from_result(self, result: Any) -> BrowserItem | None:
        if isinstance(result, Artist):
            return BrowserItem(result.title, ItemKind.ARTIST, result)
        if isinstance(result, Album):
            return BrowserItem(
                result.title, ItemKind.ALBUM, result, _safe_title(result, "parentTitle")
            )
        if _is_track(result):
            return self.track_item(result)
        return None


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


def _is_audio_playlist(playlist: Any) -> bool:
    items: Iterable[Any] = playlist.items()
    return any(_is_track(item) for item in items)
