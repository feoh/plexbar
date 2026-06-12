from pathlib import Path
from typing import Any, cast

import pytest

from plexbar import playback
from plexbar.models import BrowserItem, ItemKind, QueueTrack
from plexbar.playback import MpvPlayer, PlaybackQueue
from plexbar.plex_client import GenreArtist, PlexMusicClient
from plexbar.settings import PlexbarConfig, load_config, save_config


def test_config_round_trip_uses_restrictive_permissions(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config = PlexbarConfig(
        base_url="http://plex.example.test:32400",
        token='token"with\\escaping',
        default_library="Music",
    )

    save_config(config, config_path)

    assert load_config(config_path) == config
    assert config_path.stat().st_mode & 0o777 == 0o600


def test_invalid_config_requires_url_and_token(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[plex]\nbase_url = ""\ntoken = ""\n', encoding="utf-8")

    with pytest.raises(ValueError, match="base_url and token are required"):
        load_config(config_path)


def test_queue_append_replace_and_next() -> None:
    first = QueueTrack("One", "Artist", "Album", "http://example.test/one.mp3")
    second = QueueTrack("Two", "Artist", "Album", "http://example.test/two.mp3")
    queue = PlaybackQueue()

    queue.append([first])
    assert queue.current == first

    assert queue.replace([second, first]) == second
    assert queue.current == second
    assert queue.next() == first
    assert queue.next() is None

    queue.append([second])
    assert queue.current == second


def test_mpv_player_uses_persistent_ipc(monkeypatch: pytest.MonkeyPatch) -> None:
    track = QueueTrack("One", "Artist", "Album", "http://example.test/one.mp3")
    commands: list[list[object]] = []
    idle_active = False

    monkeypatch.setattr(playback.shutil, "which", lambda _name: "/usr/bin/mpv")
    monkeypatch.setattr(MpvPlayer, "_start_mpv", lambda _self: None)
    monkeypatch.setattr(MpvPlayer, "is_running", property(lambda _self: True))

    def fake_command(self: MpvPlayer, command: list[object]) -> dict[str, object]:
        commands.append(command)
        if command == ["get_property", "idle-active"]:
            return {"error": "success", "data": idle_active}
        return {"error": "success"}

    monkeypatch.setattr(MpvPlayer, "_command", fake_command)

    player = MpvPlayer()
    player.play(track)
    player.pause_resume()
    player.stop()

    assert commands == [
        ["loadfile", track.stream_url, "replace"],
        ["cycle", "pause"],
        ["stop"],
    ]


def test_mpv_ipc_ignores_events_after_command_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSocket:
        def __enter__(self) -> "FakeSocket":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def settimeout(self, _timeout: float) -> None:
            return None

        def connect(self, _path: str) -> None:
            return None

        def sendall(self, _request: bytes) -> None:
            return None

        def recv(self, _buffer_size: int) -> bytes:
            return (
                b'{"data":{"playlist_entry_id":1},"request_id":1,"error":"success"}\n'
                b'{"event":"start-file"}\n'
            )

    monkeypatch.setattr(playback.shutil, "which", lambda _name: "/usr/bin/mpv")
    monkeypatch.setattr(MpvPlayer, "_start_mpv", lambda _self: None)
    monkeypatch.setattr(playback.socket, "socket", lambda *_args: FakeSocket())

    player = MpvPlayer()

    assert player._request({"command": ["loadfile", "song.mp3", "replace"]}) == {
        "data": {"playlist_entry_id": 1},
        "request_id": 1,
        "error": "success",
    }


def test_mpv_player_reaps_idle_track(monkeypatch: pytest.MonkeyPatch) -> None:
    track = QueueTrack("One", "Artist", "Album", "http://example.test/one.mp3")
    idle_active = False

    monkeypatch.setattr(playback.shutil, "which", lambda _name: "/usr/bin/mpv")
    monkeypatch.setattr(MpvPlayer, "_start_mpv", lambda _self: None)
    monkeypatch.setattr(MpvPlayer, "is_running", property(lambda _self: True))

    def fake_command(_self: MpvPlayer, command: list[object]) -> dict[str, object]:
        if command == ["get_property", "idle-active"]:
            return {"error": "success", "data": idle_active}
        return {"error": "success"}

    monkeypatch.setattr(MpvPlayer, "_command", fake_command)

    player = MpvPlayer()
    player.play(track)
    assert player.reap_finished() is False

    idle_active = True
    assert player.reap_finished() is True
    assert player.reap_finished() is False


def test_browser_item_display_title() -> None:
    item = BrowserItem("Track", ItemKind.TRACK, subtitle="Artist — Album")

    assert item.display_title == "Track — Artist — Album"


def test_root_items_include_genres() -> None:
    client = PlexMusicClient.__new__(PlexMusicClient)

    assert client.root_items()[-1] == BrowserItem("Genres", ItemKind.GENRES)


def test_genres_use_track_filter_choices() -> None:
    class FakeGenre:
        title = "Jazz"

    class FakeLibrary:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def listFilterChoices(self, field: str, libtype: str) -> list[FakeGenre]:
            self.calls.append((field, libtype))
            return [FakeGenre()]

    library = FakeLibrary()
    client = cast(Any, PlexMusicClient.__new__(PlexMusicClient))
    client.library = library

    [genre] = client.genres()

    assert genre.title == "Jazz"
    assert genre.kind is ItemKind.GENRE
    assert isinstance(genre.source, FakeGenre)
    assert library.calls == [("genre", "track")]


def test_genre_browse_items_offer_artist_and_album_grouping() -> None:
    genre = object()
    client = PlexMusicClient.__new__(PlexMusicClient)

    assert client.genre_browse_items(genre) == [
        BrowserItem("By Artist", ItemKind.GENRE_ARTISTS, genre),
        BrowserItem("By Album", ItemKind.GENRE_ALBUMS, genre),
    ]


def test_genre_artists_are_deduplicated_from_matching_tracks() -> None:
    class FakeGenre:
        pass

    class FakeArtist:
        title = "The Artist"
        ratingKey = "artist-1"

    class FakeTrack:
        TYPE = "track"

        def artist(self) -> FakeArtist:
            return FakeArtist()

    class FakeLibrary:
        def search(self, libtype: str, genre: FakeGenre) -> list[FakeTrack]:
            return [FakeTrack(), FakeTrack()]

    genre = FakeGenre()
    client = cast(Any, PlexMusicClient.__new__(PlexMusicClient))
    client.library = FakeLibrary()

    [artist] = client.artists_for_genre(genre)

    assert artist.title == "The Artist"
    assert artist.kind is ItemKind.GENRE_ARTIST
    assert artist.source is not None
    assert artist.source[0] is genre
    assert artist.source[1] == GenreArtist("The Artist", "artist-1")
