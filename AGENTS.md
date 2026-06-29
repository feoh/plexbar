# Plexbar agent notes

## Architecture overview

- **Textual TUI (`src/plexbar/app.py`)**: `PlexbarApp` owns the UI,
  keybindings, browsing state, now-playing display, queue rendering, and setup
  flow. It composes a browser pane, side pane, now-playing label, cover art
  widget, queue widget, status line, and footer.
- **Playback queue (`src/plexbar/playback.py`)**: `PlaybackQueue` is an
  in-memory queue. It tracks the currently playing item separately from the
  next unplayed item so appending tracks does not imply they are playing.
- **mpv integration (`src/plexbar/playback.py`)**: `MpvPlayer` manages a
  persistent `mpv` process and controls it over JSON IPC. Avoid tests that
  require a real `mpv`; monkeypatch `shutil.which`, `_start_mpv`, `_command`,
  or use fake player objects.
- **Plex access (`src/plexbar/plex_client.py`)**: `PlexMusicClient` wraps
  PlexAPI and converts Plex objects into `BrowserItem` / `QueueTrack` data for
  the TUI.
- **Data models (`src/plexbar/models.py`)**: `BrowserItem`, `ItemKind`, and
  `QueueTrack` are the shared boundary objects between Plex browsing, queueing,
  and UI rendering.
- **Settings (`src/plexbar/settings.py`)**: config loading/saving lives here;
  keep credentials out of tests and commits.

## Testing Textual behavior

Use Textual's `App.run_test()` for UI state and keybinding behavior. It is
especially useful for reproducing real user flows such as adding tracks,
pressing `f` to focus now playing, pressing `s` to stop, or checking that
widgets render the expected labels.

Typical pattern:

```python
async def scenario() -> None:
    monkeypatch.setattr(app.PlexbarApp, "on_mount", lambda _self: None)
    plexbar = app.PlexbarApp()
    async with plexbar.run_test() as pilot:
        plexbar.client = fake_client
        plexbar.player = fake_player
        plexbar.query_one("#search", app.Input).display = False
        plexbar.query_one("#browser-list", app.ListView).focus()

        await pilot.press("f")
        await pilot.pause()

asyncio.run(scenario())
```

Notes:

- Patch `PlexbarApp.on_mount` in run-test scenarios unless the test
  intentionally exercises setup/connection. The real mount path may load user
  config, connect to Plex, or start `mpv`.
- The composed search `Input` can consume normal letter keys. Hide `#search`
  and focus `#browser-list` before `pilot.press("f")`, `pilot.press("s")`, or
  other app-level letter bindings.
- Use fake `client` and `player` objects for app tests so the real Textual DOM
  and binding dispatch are exercised without network or subprocess I/O.
- Assert `Static` widget text with `str(widget.render())`.
- Textual style assignments like `"auto"` become style scalar objects; assert
  with `str(widget.styles.width)` or `str(widget.styles.height)`.

## Local validation

Use `uv` for all Python commands:

```bash
uv run pytest -q
uv run pre-commit run --all-files
uv run mypy src/plexbar tests
```
