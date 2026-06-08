from pathlib import Path

import pytest

from plexbar.models import BrowserItem, ItemKind, QueueTrack
from plexbar.playback import PlaybackQueue
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


def test_browser_item_display_title() -> None:
    item = BrowserItem("Track", ItemKind.TRACK, subtitle="Artist — Album")

    assert item.display_title == "Track — Artist — Album"
