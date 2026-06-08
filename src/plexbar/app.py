"""Textual application for Plexbar."""

from collections.abc import Callable

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
        width: 70;
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

    def compose(self) -> ComposeResult:
        with Vertical(id="setup-box"):
            yield Label("Plexbar first-run setup")
            yield Label("Plex base URL")
            yield Input(placeholder="http://127.0.0.1:32400", id="plex-url")
            yield Label("Plex token")
            yield Input(password=True, placeholder="Your Plex token", id="plex-token")
            yield Button("Validate connection", id="validate", variant="primary")
            yield Static("Enter your Plex connection details.", id="setup-status")
            yield ListView(id="library-list")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#library-list", ListView).display = False
        self.query_one("#plex-url", Input).focus()

    @on(Button.Pressed, "#validate")
    def validate_connection(self) -> None:
        """Validate credentials and load music libraries."""

        base_url = self.query_one("#plex-url", Input).value.strip()
        token = self.query_one("#plex-token", Input).value.strip()
        if not base_url or not token:
            self._set_status("Plex URL and token are required.")
            return
        self._base_url = base_url
        self._token = token
        self._set_status("Validating Plex connection…")
        self._validate_connection(base_url, token)

    @work(thread=True)
    def _validate_connection(self, base_url: str, token: str) -> None:
        try:
            libraries = PlexMusicClient.validate(base_url, token)
        except Exception as exc:  # noqa: BLE001 - display connection failures to user
            self.app.call_from_thread(self._show_validation_error, str(exc))
            return
        self.app.call_from_thread(self._show_libraries, libraries)

    def _show_validation_error(self, message: str) -> None:
        self._set_status(f"Connection failed: {message}")

    def _show_libraries(self, libraries: list[str]) -> None:
        self._libraries = libraries
        if not libraries:
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
        Binding("space", "pause_resume", "Pause/resume"),
        Binding("n", "next_track", "Next"),
        Binding("s", "stop", "Stop"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.config: PlexbarConfig | None = None
        self.client: PlexMusicClient | None = None
        self.player: MpvPlayer | None = None
        self.queue = PlaybackQueue()
        self.history: list[list[BrowserItem]] = []
        self.items: list[BrowserItem] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(placeholder="Search Plex music…", id="search")
        with Horizontal():
            with Vertical(id="browser"):
                yield Label("Browse", classes="panel-title")
                yield ListView(id="browser-list")
            with Vertical(id="side"):
                yield Label("Now Playing", classes="panel-title")
                yield Static("Nothing playing", id="now-playing")
                yield Label("Queue", classes="panel-title")
                yield Static("Queue is empty", id="queue")
        yield Static("Starting Plexbar…", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#search", Input).display = False
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
        elif item.kind is ItemKind.ARTIST:
            self.show_items(self.client.albums(item.source), f"Albums by {item.title}")
        elif item.kind in {ItemKind.ALBUM, ItemKind.PLAYLIST}:
            self.show_items(self.client.tracks(item.source), item.title)
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
        track = self.queue.next()
        if track is None:
            self.set_status("End of queue.")
            self.refresh_queue()
            return
        self.play(track)
        self.refresh_queue()

    def action_stop(self) -> None:
        if self.player is not None:
            self.player.stop()
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
        self.player.play(track)
        self.query_one("#now-playing", Static).update(track.label)
        self.set_status(f"Playing {track.label}")

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
