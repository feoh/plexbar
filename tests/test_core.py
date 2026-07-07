import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from plexbar import app, playback
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


def make_track(title: str) -> QueueTrack:
    return QueueTrack(
        title,
        "Artist",
        "Album",
        f"http://example.test/{title.lower()}.mp3",
    )


def test_queue_append_waits_for_playback_before_marking_current() -> None:
    first = make_track("One")
    second = make_track("Two")
    queue = PlaybackQueue()

    queue.append([first, second])

    assert queue.current is None
    assert queue.labels() == [f"  {first.label}", f"  {second.label}"]
    assert queue.next() == first
    assert queue.current == first
    assert queue.labels() == [f"▶ {first.label}", f"  {second.label}"]
    assert queue.next() == second
    assert queue.next() is None


def test_queue_replace_starts_first_track_and_advances_to_second() -> None:
    first = make_track("One")
    second = make_track("Two")
    queue = PlaybackQueue()

    assert queue.replace([first, second]) == first
    assert queue.current == first
    assert queue.next() == second
    assert queue.next() is None


def test_queue_append_after_end_advances_to_new_tracks_only() -> None:
    first = make_track("One")
    second = make_track("Two")
    queue = PlaybackQueue()

    assert queue.replace([first]) == first
    assert queue.next() is None

    queue.append([second])

    assert queue.current is None
    assert queue.next() == second


def test_queue_clear_current_keeps_next_track_ready() -> None:
    first = make_track("One")
    second = make_track("Two")
    queue = PlaybackQueue()

    assert queue.replace([first, second]) == first
    queue.clear_current()

    assert queue.current is None
    assert queue.next() == second


def test_app_append_and_focus_preserve_now_playing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = make_track("One")
    second = make_track("Two")
    played: list[QueueTrack] = []

    async def scenario() -> None:
        monkeypatch.setattr(app.PlexbarApp, "on_mount", lambda _self: None)
        plexbar = app.PlexbarApp()
        async with plexbar.run_test() as pilot:
            plexbar.client = cast(
                Any,
                SimpleNamespace(playable_tracks=lambda _item: [second]),
            )
            plexbar.player = cast(
                Any,
                SimpleNamespace(play=lambda track: played.append(track)),
            )

            plexbar.query_one("#search", app.Input).display = False
            plexbar.query_one("#browser-list", app.ListView).focus()
            plexbar.queue.replace([first])
            plexbar.play(first)
            plexbar.append_item(BrowserItem("Two", ItemKind.TRACK))
            await pilot.press("f")
            await pilot.pause()

            assert plexbar.current_track == first
            assert played == [first]
            assert str(plexbar.query_one("#now-playing", app.Static).render()) == (
                first.label
            )
            assert str(plexbar.query_one("#queue", app.Static).render()) == (
                f"▶ {first.label}\n  {second.label}"
            )
            assert str(plexbar.query_one("#status", app.Static).render()) == (
                "Added 1 track(s) to queue."
            )
            assert not plexbar.query_one("#browser", app.Vertical).display
            cover_art = plexbar.query_one("#cover-art", app.AutoImage)
            assert str(cover_art.styles.width) == "auto"
            assert str(cover_art.styles.height) == "auto"

    asyncio.run(scenario())


def test_app_stop_clears_playback_state_without_rewinding_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = make_track("One")
    second = make_track("Two")
    stop_calls: list[object] = []

    async def scenario() -> None:
        monkeypatch.setattr(app.PlexbarApp, "on_mount", lambda _self: None)
        plexbar = app.PlexbarApp()
        async with plexbar.run_test() as pilot:
            plexbar.player = cast(
                Any,
                SimpleNamespace(stop=lambda: stop_calls.append(object())),
            )
            plexbar.query_one("#search", app.Input).display = False
            plexbar.query_one("#browser-list", app.ListView).focus()
            plexbar.current_track = first
            plexbar.queue.replace([first, second])
            await pilot.press("f")
            await pilot.press("s")
            await pilot.pause()

            assert len(stop_calls) == 1
            assert plexbar.current_track is None
            assert plexbar.queue.current is None
            assert plexbar.queue.next() == second
            assert str(plexbar.query_one("#now-playing", app.Static).render()) == (
                "Nothing playing"
            )
            assert str(plexbar.query_one("#queue", app.Static).render()) == (
                f"  {first.label}\n  {second.label}"
            )
            assert str(plexbar.query_one("#status", app.Static).render()) == "Stopped."
            assert plexbar.query_one("#browser", app.Vertical).display
            assert plexbar.query_one("#browser-list", app.ListView).has_focus

    asyncio.run(scenario())


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
    assert not player.reap_finished()

    idle_active = True
    assert player.reap_finished()
    assert not player.reap_finished()


def test_browser_item_display_title() -> None:
    item = BrowserItem("Track", ItemKind.TRACK, subtitle="Artist — Album")

    assert item.display_title == "Track — Artist — Album"


def test_prepare_auth_url_copies_and_opens_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    copied_urls: list[str] = []
    opened_calls: list[tuple[str, int, bool]] = []

    monkeypatch.setattr(app.pyperclip, "copy", copied_urls.append)

    def fake_open(url: str, new: int, autoraise: bool) -> bool:
        opened_calls.append((url, new, autoraise))
        return True

    monkeypatch.setattr(app.webbrowser, "open", fake_open)

    messages = app.prepare_auth_url("https://plex.example.test/auth")

    expected_opened_calls = [("https://plex.example.test/auth", 1, True)]

    assert copied_urls == ["https://plex.example.test/auth"]
    assert opened_calls == expected_opened_calls
    assert messages == [
        "Copied the sign-in URL to your clipboard.",
        "Opened your browser for Plex sign-in.",
    ]


def test_prepare_auth_url_reports_clipboard_and_browser_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_copy(_url: str) -> None:
        raise RuntimeError("no clipboard")

    monkeypatch.setattr(app.pyperclip, "copy", fake_copy)
    monkeypatch.setattr(app.webbrowser, "open", lambda *_args, **_kwargs: False)

    messages = app.prepare_auth_url("https://plex.example.test/auth")

    assert messages == [
        "Could not copy the sign-in URL to the clipboard: no clipboard",
        "Could not open your browser automatically.",
    ]


def test_root_items_include_genres() -> None:
    client = PlexMusicClient.__new__(PlexMusicClient)

    assert client.root_items()[-1] == BrowserItem("Genres", ItemKind.GENRES)


def test_artist_and_album_browse_items_put_recently_added_first() -> None:
    artist = SimpleNamespace(title="Artist")
    album = SimpleNamespace(title="Album", parentTitle="Artist")

    class FakeLibrary:
        def search(self, libtype: str) -> list[Any]:
            if libtype == "artist":
                return [artist]
            return []

        def albums(self) -> list[Any]:
            return [album]

    client = cast(Any, PlexMusicClient.__new__(PlexMusicClient))
    client.library = FakeLibrary()

    assert client.artist_browse_items() == [
        BrowserItem("Recently Added", ItemKind.ARTISTS_RECENTLY_ADDED),
        BrowserItem("Artist", ItemKind.ARTIST, artist),
    ]
    assert client.album_browse_items() == [
        BrowserItem("Recently Added", ItemKind.ALBUMS_RECENTLY_ADDED),
        BrowserItem("Album", ItemKind.ALBUM, album, "Artist"),
    ]


def test_recently_added_artists_and_albums_use_plex_recently_added() -> None:
    artist = SimpleNamespace(title="New Artist")
    album = SimpleNamespace(title="New Album", parentTitle="New Artist")
    calls: list[str] = []

    class FakeLibrary:
        def recentlyAdded(self, libtype: str) -> list[Any]:
            calls.append(libtype)
            if libtype == "artist":
                return [artist]
            if libtype == "album":
                return [album]
            return []

    client = cast(Any, PlexMusicClient.__new__(PlexMusicClient))
    client.library = FakeLibrary()

    assert client.recently_added_artists() == [
        BrowserItem("New Artist", ItemKind.ARTIST, artist)
    ]
    assert client.recently_added_albums() == [
        BrowserItem("New Album", ItemKind.ALBUM, album, "New Artist")
    ]
    assert calls == ["artist", "album"]


def test_select_item_routes_artist_and_album_browse_modes() -> None:
    artists_menu = [
        BrowserItem("Recently Added", ItemKind.ARTISTS_RECENTLY_ADDED),
        BrowserItem("Artist", ItemKind.ARTIST),
    ]
    albums_menu = [
        BrowserItem("Recently Added", ItemKind.ALBUMS_RECENTLY_ADDED),
        BrowserItem("Album", ItemKind.ALBUM),
    ]
    recent_artists = [BrowserItem("New Artist", ItemKind.ARTIST)]
    recent_albums = [BrowserItem("New Album", ItemKind.ALBUM)]

    client = SimpleNamespace(
        artist_browse_items=lambda: artists_menu,
        album_browse_items=lambda: albums_menu,
        recently_added_artists=lambda: recent_artists,
        recently_added_albums=lambda: recent_albums,
    )
    plexbar = cast(Any, app.PlexbarApp())
    plexbar.client = client
    shown: list[tuple[list[BrowserItem], str]] = []

    def show_items(
        items: list[BrowserItem], status: str, *, remember: bool = True
    ) -> None:
        shown.append((items, status))

    plexbar.show_items = show_items

    plexbar.select_item(BrowserItem("Artists", ItemKind.ARTISTS))
    plexbar.select_item(BrowserItem("Albums", ItemKind.ALBUMS))
    plexbar.select_item(BrowserItem("Recently Added", ItemKind.ARTISTS_RECENTLY_ADDED))
    plexbar.select_item(BrowserItem("Recently Added", ItemKind.ALBUMS_RECENTLY_ADDED))

    expected = [
        (artists_menu, "Artists"),
        (albums_menu, "Albums"),
        (recent_artists, "Recently Added Artists"),
        (recent_albums, "Recently Added Albums"),
    ]

    assert shown == expected


def test_genres_use_track_filter_choices() -> None:
    class FakeGenre:
        title = "Jazz"

    calls: list[tuple[str, str]] = []

    def list_filter_choices(field: str, libtype: str) -> list[FakeGenre]:
        calls.append((field, libtype))
        return [FakeGenre()]

    client = cast(Any, PlexMusicClient.__new__(PlexMusicClient))
    client.library = SimpleNamespace(listFilterChoices=list_filter_choices)

    [genre] = client.genres()
    expected_calls = [("genre", "track")]

    assert genre.title == "Jazz"
    assert genre.kind is ItemKind.GENRE
    assert isinstance(genre.source, FakeGenre)
    assert calls == expected_calls


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
