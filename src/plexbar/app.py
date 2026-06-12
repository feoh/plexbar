"""Textual application for Plexbar."""

import webbrowser
from collections.abc import Callable
from io import BytesIO
from urllib.request import urlopen

import pyperclip  # type: ignore[import-untyped]
from plexapi.myplex import MyPlexPinLogin  # type: ignore[import-untyped]
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Static,
)
from textual_image.widget import AutoImage

from plexbar.models import BrowserItem, ItemKind, QueueTrack
from plexbar.playback import MpvNotFoundError, MpvPlayer, PlaybackQueue
from plexbar.plex_client import PlexMusicClient
from plexbar.settings import (
    CONFIG_PATH,
    PlexbarConfig,
    config_exists,
    load_config,
    save_config,
)


def prepare_auth_url(auth_url: str) -> list[str]:
    """Copy and open the Plex OAuth URL, returning user-visible status lines."""

    messages: list[str] = []
    try:
        pyperclip.copy(auth_url)
    except Exception as exc:  # noqa: BLE001 - clipboard support varies by platform
        messages.append(f"Could not copy the sign-in URL to the clipboard: {exc}")
    else:
        messages.append("Copied the sign-in URL to your clipboard.")

    try:
        opened = webbrowser.open(auth_url, new=1, autoraise=True)
    except Exception as exc:  # noqa: BLE001 - browser launch support varies by platform
        messages.append(f"Could not open your browser automatically: {exc}")
    else:
        if opened:
            messages.append("Opened your browser for Plex sign-in.")
        else:
            messages.append("Could not open your browser automatically.")

    return messages


class BrowserRow(ListItem):
    """List item that carries a BrowserItem payload."""

    def __init__(self, item: BrowserItem) -> None:
        super().__init__(Label(item.display_title))
        self.item = item


class SetupScreen(Screen[None]):
    """First-run Plex configuration screen."""

    CSS = """
    SetupScreen {
        align: center middle;
    }

    #setup-box {
        width: 90;
        height: auto;
        border: round $accent;
        padding: 1 2;
    }

    #setup-status {
        min-height: 3;
    }
    """

    def __init__(self, on_configured: Callable[[PlexbarConfig], None]) -> None:
        super().__init__()
        self._on_configured = on_configured
        self._base_url = ""
        self._token = ""
        self._libraries: list[str] = []
        self._auth_in_progress = False

    def compose(self) -> ComposeResult:
        with Vertical(id="setup-box"):
            yield Label("Plexbar first-run setup")
            yield Label("Plex base URL")
            yield Input(placeholder="http://127.0.0.1:32400", id="plex-url")
            yield Button("Sign in with Plex", id="plex-sign-in", variant="primary")
            yield Static(
                "Enter your Plex server URL, then sign in with Plex to authorize Plexbar.",
                id="setup-status",
            )
            yield ListView(id="library-list")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#library-list", ListView).display = False
        self.query_one("#plex-url", Input).focus()

    @on(Button.Pressed, "#plex-sign-in")
    def sign_in_with_plex(self) -> None:
        """Start Plex OAuth sign-in and validate the resulting token."""

        if self._auth_in_progress:
            return
        base_url = self.query_one("#plex-url", Input).value.strip()
        if not base_url:
            self._set_status("Plex URL is required.")
            return
        self._base_url = base_url
        self._auth_in_progress = True
        self.query_one("#plex-sign-in", Button).disabled = True
        self._set_status("Requesting Plex sign-in link…")
        self._sign_in_with_plex(base_url)

    @work(thread=True)
    def _sign_in_with_plex(self, base_url: str) -> None:
        try:
            pin_login = MyPlexPinLogin(oauth=True)
            auth_url = pin_login.oauthUrl()
            launch_messages = prepare_auth_url(auth_url)
            self.app.call_from_thread(self._show_auth_url, auth_url, launch_messages)
            pin_login.run(timeout=300)
            if not pin_login.waitForLogin() or not pin_login.token:
                self.app.call_from_thread(
                    self._show_validation_error,
                    "Plex sign-in timed out or was cancelled.",
                )
                return
            token = str(pin_login.token)
            libraries = PlexMusicClient.validate(base_url, token)
        except Exception as exc:  # noqa: BLE001 - display connection failures to user
            self.app.call_from_thread(self._show_validation_error, str(exc))
            return
        self.app.call_from_thread(self._show_libraries, token, libraries)

    def _show_auth_url(self, auth_url: str, launch_messages: list[str]) -> None:
        launch_status = "\n".join(launch_messages)
        self._set_status(
            "Sign in with Plex to authorize Plexbar.\n"
            f"{launch_status}\n"
            f"If needed, open this URL in your browser:\n{auth_url}\n"
            "Waiting for authorization…"
        )

    def _show_validation_error(self, message: str) -> None:
        self._auth_in_progress = False
        self.query_one("#plex-sign-in", Button).disabled = False
        self._set_status(f"Connection failed: {message}")

    def _show_libraries(self, token: str, libraries: list[str]) -> None:
        self._auth_in_progress = False
        self._token = token
        self._libraries = libraries
        if not libraries:
            self.query_one("#plex-sign-in", Button).disabled = False
            self._set_status("Connected, but no music libraries were found.")
            return
        library_list = self.query_one("#library-list", ListView)
        library_list.clear()
        for library in libraries:
            library_list.append(BrowserRow(BrowserItem(library, ItemKind.ROOT)))
        library_list.display = True
        library_list.focus()
        self._set_status("Select a default music library and press Enter.")

    @on(ListView.Selected, "#library-list")
    def save_selected_library(self, event: ListView.Selected) -> None:
        """Persist selected library and continue into the app."""

        row = event.item
        if not isinstance(row, BrowserRow):
            return
        config = PlexbarConfig(
            base_url=self._base_url,
            token=self._token,
            default_library=row.item.title,
        )
        save_config(config)
        self._on_configured(config)
        self.dismiss()

    def _set_status(self, message: str) -> None:
        self.query_one("#setup-status", Static).update(message)


class PlexbarApp(App[None]):
    """Plex music player TUI."""

    CSS = """
    #browser {
        width: 2fr;
        border: round $primary;
    }

    #side {
        width: 1fr;
        border: round $secondary;
    }

    #search {
        dock: top;
    }

    #status {
        dock: bottom;
        height: 3;
        border-top: solid $primary;
    }

    #cover-art {
        width: 100%;
        height: 18;
        margin: 0 1 1 1;
    }

    .panel-title {
        text-style: bold;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("/", "search", "Search"),
        Binding("enter", "select", "Select/enqueue"),
        Binding("p", "play_now", "Play now"),
        Binding("a", "append", "Append"),
        Binding("space", "pause_resume", "Pause/resume", priority=True),
        Binding("n", "next_track", "Next", priority=True),
        Binding("s", "stop", "Stop", priority=True),
        Binding("f", "toggle_focus", "Focus now playing", priority=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.config: PlexbarConfig | None = None
        self.client: PlexMusicClient | None = None
        self.player: MpvPlayer | None = None
        self.queue = PlaybackQueue()
        self.current_track: QueueTrack | None = None
        self.history: list[list[BrowserItem]] = []
        self.items: list[BrowserItem] = []
        self._focus_now_playing = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(placeholder="Search Plex music…", id="search")
        with Horizontal():
            with Vertical(id="browser"):
                yield Label("Browse", classes="panel-title")
                yield ListView(id="browser-list")
            with Vertical(id="side"):
                yield Label("Now Playing", classes="panel-title")
                yield AutoImage(id="cover-art")
                yield Static("Nothing playing", id="now-playing")
                yield Label("Queue", classes="panel-title")
                yield Static("Queue is empty", id="queue")
        yield Static("Starting Plexbar…", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#search", Input).display = False
        self.query_one("#cover-art", AutoImage).display = False
        self.set_interval(0.5, self.advance_finished_track)
        if config_exists():
            try:
                self.start_with_config(load_config())
            except Exception as exc:  # noqa: BLE001 - show config failures in TUI
                self.set_status(f"Failed to load {CONFIG_PATH}: {exc}")
                self.push_screen(SetupScreen(self.start_with_config))
        else:
            self.push_screen(SetupScreen(self.start_with_config))

    def start_with_config(self, config: PlexbarConfig) -> None:
        """Connect to Plex and initialize playback/browsing."""

        self.config = config
        self.set_status("Connecting to Plex…")
        self._connect(config)

    @work(thread=True)
    def _connect(self, config: PlexbarConfig) -> None:
        try:
            client = PlexMusicClient(config)
            player = MpvPlayer()
        except MpvNotFoundError as exc:
            self.call_from_thread(self.set_status, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - show connection failures in TUI
            self.call_from_thread(self.set_status, f"Plex connection failed: {exc}")
            return
        self.call_from_thread(self._connected, client, player)

    def _connected(self, client: PlexMusicClient, player: MpvPlayer) -> None:
        self.client = client
        self.player = player
        self.show_items(
            client.root_items(), "Connected. Choose a section.", remember=False
        )

    def show_items(
        self, items: list[BrowserItem], status: str, *, remember: bool = True
    ) -> None:
        """Display browser items."""

        if remember and self.items:
            self.history.append(self.items)
        self.items = items
        browser = self.query_one("#browser-list", ListView)
        browser.clear()
        if self.history:
            browser.append(BrowserRow(BrowserItem("..", ItemKind.BACK)))
        for item in items:
            browser.append(BrowserRow(item))
        browser.focus()
        self.set_status(status)

    async def action_quit(self) -> None:
        """Stop playback before exiting Plexbar."""

        if self.player is not None:
            self.player.close()
        self.exit()

    def action_search(self) -> None:
        search = self.query_one("#search", Input)
        search.display = True
        search.focus()

    @on(Input.Submitted, "#search")
    def perform_search(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        search = self.query_one("#search", Input)
        search.display = False
        search.value = ""
        if not query or self.client is None:
            return
        self.show_items(self.client.search(query), f"Search results for '{query}'.")

    @on(ListView.Selected, "#browser-list")
    def select_browser_row(self, event: ListView.Selected) -> None:
        """Handle Enter on the browser list.

        Textual's ListView consumes Enter and emits ``Selected`` instead of
        letting the app-level Enter binding run, so route the selected row
        through the same selection logic used by the explicit action.
        """

        row = event.item
        if isinstance(row, BrowserRow):
            self.select_item(row.item)

    def action_select(self) -> None:
        item = self.focused_item()
        if item is not None:
            self.select_item(item)

    def select_item(self, item: BrowserItem) -> None:
        """Select, drill into, or enqueue a browser item."""

        if self.client is None:
            return
        if item.kind is ItemKind.BACK:
            self.go_back()
        elif item.kind is ItemKind.ARTISTS:
            self.show_items(self.client.artists(), "Artists")
        elif item.kind is ItemKind.ALBUMS:
            self.show_items(self.client.albums(), "Albums")
        elif item.kind is ItemKind.TRACKS:
            self.show_items(self.client.tracks(), "Tracks")
        elif item.kind is ItemKind.PLAYLISTS:
            self.show_items(self.client.playlists(), "Playlists")
        elif item.kind is ItemKind.GENRES:
            self.show_items(self.client.genres(), "Genres")
        elif item.kind is ItemKind.ARTIST:
            self.show_items(self.client.albums(item.source), f"Albums by {item.title}")
        elif item.kind in {ItemKind.ALBUM, ItemKind.PLAYLIST}:
            self.show_items(self.client.tracks(item.source), item.title)
        elif item.kind is ItemKind.GENRE:
            self.show_items(self.client.genre_browse_items(item.source), item.title)
        elif item.kind is ItemKind.GENRE_ARTISTS:
            self.show_items(self.client.artists_for_genre(item.source), item.title)
        elif item.kind is ItemKind.GENRE_ALBUMS:
            self.show_items(self.client.albums_for_genre(item.source), item.title)
        elif item.kind is ItemKind.GENRE_ARTIST and item.source is not None:
            genre, artist = item.source
            self.show_items(self.client.albums_for_genre(genre, artist), item.title)
        elif item.kind is ItemKind.GENRE_ALBUM and item.source is not None:
            genre, album = item.source
            self.show_items(
                self.client.tracks_for_genre_album(genre, album), item.title
            )
        elif item.kind is ItemKind.TRACK:
            self.append_item(item)

    def action_play_now(self) -> None:
        item = self.focused_item()
        if item is None or self.client is None:
            return
        tracks = self.client.playable_tracks(item)
        first = self.queue.replace(tracks)
        if first is None:
            self.set_status(f"Nothing playable for {item.title}.")
            return
        self.play(first)
        self.refresh_queue()

    def action_append(self) -> None:
        item = self.focused_item()
        if item is not None:
            self.append_item(item)

    def action_pause_resume(self) -> None:
        if self.player is not None:
            self.player.pause_resume()

    def action_next_track(self) -> None:
        self.play_next_track("End of queue.")

    def advance_finished_track(self) -> None:
        """Continue playback when mpv exits at the end of a track."""

        if self.player is None or not self.player.reap_finished():
            return
        self.play_next_track("End of queue.")

    def play_next_track(self, end_status: str) -> None:
        """Advance the queue and play the next track, if any."""

        track = self.queue.next()
        if track is None:
            self.clear_now_playing()
            self.set_status(end_status)
            self.refresh_queue()
            return
        self.play(track)
        self.refresh_queue()

    def action_toggle_focus(self) -> None:
        """Hide the browse pane so now-playing fills the screen."""

        self._focus_now_playing = not self._focus_now_playing
        self.query_one("#browser", Vertical).display = not self._focus_now_playing
        cover_art = self.query_one("#cover-art", AutoImage)
        if self._focus_now_playing:
            cover_art.styles.width = "auto"
            cover_art.styles.height = "auto"
        else:
            cover_art.styles.width = "100%"
            cover_art.styles.height = 18

    def action_stop(self) -> None:
        if self.player is not None:
            self.player.stop()
        self.clear_now_playing()
        self.set_status("Stopped.")

    def append_item(self, item: BrowserItem) -> None:
        if self.client is None:
            return
        tracks = self.client.playable_tracks(item)
        if not tracks:
            self.set_status(f"Nothing playable for {item.title}.")
            return
        self.queue.append(tracks)
        self.refresh_queue()
        self.set_status(f"Added {len(tracks)} track(s) to queue.")

    def play(self, track: QueueTrack) -> None:
        if self.player is None:
            self.set_status("mpv is not available.")
            return
        self.current_track = track
        self.player.play(track)
        self.query_one("#now-playing", Static).update(track.label)
        self.show_cover_art(track)
        self.set_status(f"Playing {track.label}")

    def show_cover_art(self, track: QueueTrack) -> None:
        """Load and display the current track's Plex artwork."""

        cover_art = self.query_one("#cover-art", AutoImage)
        cover_art.image = None
        cover_art.display = False
        if track.artwork_url:
            self._load_cover_art(track.artwork_url)

    @work(thread=True)
    def _load_cover_art(self, artwork_url: str) -> None:
        try:
            with urlopen(artwork_url, timeout=10) as response:
                image_bytes = response.read()
        except Exception:  # noqa: BLE001 - missing artwork should not stop playback
            return
        self.call_from_thread(self._set_cover_art, artwork_url, image_bytes)

    def _set_cover_art(self, artwork_url: str, image_bytes: bytes) -> None:
        if self.current_track is None or self.current_track.artwork_url != artwork_url:
            return
        cover_art = self.query_one("#cover-art", AutoImage)
        cover_art.image = BytesIO(image_bytes)
        cover_art.display = True

    def clear_now_playing(self) -> None:
        """Clear the now-playing label and cover art."""

        self.current_track = None
        self.query_one("#now-playing", Static).update("Nothing playing")
        cover_art = self.query_one("#cover-art", AutoImage)
        cover_art.image = None
        cover_art.display = False
        if self._focus_now_playing:
            self._focus_now_playing = False
            self.query_one("#browser", Vertical).display = True
            cover_art.styles.width = "100%"
            cover_art.styles.height = 18
            self.query_one("#browser-list", ListView).focus()

    def focused_item(self) -> BrowserItem | None:
        browser = self.query_one("#browser-list", ListView)
        row = browser.highlighted_child
        if isinstance(row, BrowserRow):
            return row.item
        return None

    def go_back(self) -> None:
        if not self.history:
            return
        self.items = self.history.pop()
        browser = self.query_one("#browser-list", ListView)
        browser.clear()
        if self.history:
            browser.append(BrowserRow(BrowserItem("..", ItemKind.BACK)))
        for item in self.items:
            browser.append(BrowserRow(item))
        self.set_status("Back")

    def refresh_queue(self) -> None:
        labels = self.queue.labels()
        self.query_one("#queue", Static).update(
            "\n".join(labels) if labels else "Queue is empty"
        )

    def set_status(self, message: str) -> None:
        self.query_one("#status", Static).update(message)


def main() -> None:
    """Run Plexbar."""

    PlexbarApp().run()
