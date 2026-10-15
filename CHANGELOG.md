# Changelog

## [1.2.9] - 2026-10-15

### Changed

- Run searches in the background with a "Searching…" status so the app stays responsive, and report search failures in the status line instead of exiting.
- Match artist, album, and playlist names regardless of case, accents, and typographic punctuation, so `Yo-Yo Ma` finds "Yo‐Yo Ma", `guns n' roses` finds "Guns N’ Roses", and `bjork` finds "Björk". Track titles are still matched by the Plex server.
- List empty music playlists in the Playlists view.

### Fixed

- Speed up search and the Playlists view by asking Plex for music playlists directly instead of downloading every playlist's tracks.

## [1.2.8] - 2026-09-04

### Changed

- Separate queue replacement from playback: `P` replaces the queue with the focused selection, while `p` starts the queued tracks and `n` continues to advance to the next track.

## [1.2.7] - 2026-08-28

### Added

- Add the MIT license to the repository and package.

### Fixed

- Restore keyboard focus to the browser after submitting an empty search so arrow-key navigation continues to work.

## [1.2.6] - 2026-08-25

### Fixed

- Scale and center full-screen cover art within the available terminal space while preserving its original aspect ratio, preventing clipping.
- Keep the queue visible when no cover artwork is loaded by preventing the empty cover-art container from reserving layout space.

[1.2.9]: https://github.com/feoh/plexbar/compare/v1.2.8...v1.2.9
[1.2.8]: https://github.com/feoh/plexbar/compare/v1.2.7...v1.2.8
[1.2.7]: https://github.com/feoh/plexbar/compare/v1.2.6...v1.2.7
[1.2.6]: https://github.com/feoh/plexbar/compare/v1.2.5...v1.2.6
