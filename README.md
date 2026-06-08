# Plexbar

Plexbar is a command-line TUI music player for Plex. It lets you browse and play
Plex music without opening a web browser.

## Status

This is an MVP skeleton. It supports first-run configuration, basic Plex music
browsing, queue management, and one-track-at-a-time playback through local
`mpv`.

## Requirements

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/)
- `mpv` installed and available on `PATH`
- A Plex server with a music library
- A Plex token

## Install dependencies

```bash
uv sync
```

## Run

```bash
uv run plexbar
```

On first launch, Plexbar prompts for:

1. Plex base URL, for example `http://127.0.0.1:32400`
2. Plex token
3. Default music library

Configuration is saved to:

```text
~/.config/plexbar/config.toml
```

The file is written with user-only permissions where supported.

## Browsing

The top-level browser includes:

- Artists
- Albums
- Tracks
- Playlists

Press `Enter` to drill down through artists, albums, and playlists. Press
`Enter` on a track to enqueue it.

## Keybindings

| Key | Action |
| --- | --- |
| `/` | Search Plex music |
| `Enter` | Select/drill down, or enqueue focused track |
| `p` | Play focused track/album/artist/playlist immediately and replace queue |
| `a` | Append focused track/album/artist/playlist to queue |
| `Space` | Pause/resume |
| `n` | Next track |
| `s` | Stop playback |
| `q` | Quit |

## Notes

Plexbar currently controls `mpv` by launching it as a local subprocess for each
track. Future versions may switch to mpv IPC for richer playback state and
automatic end-of-track advancement.
