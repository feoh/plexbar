"""Configuration loading and first-run setup persistence."""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_dir

APP_NAME = "plexbar"
CONFIG_DIR = Path(user_config_dir(APP_NAME))
CONFIG_PATH = CONFIG_DIR / "config.toml"


@dataclass(frozen=True)
class PlexbarConfig:
    """Persisted Plexbar configuration."""

    base_url: str
    token: str
    default_library: str | None = None


def config_exists(path: Path = CONFIG_PATH) -> bool:
    """Return whether a Plexbar config file exists."""

    return path.exists()


def load_config(path: Path = CONFIG_PATH) -> PlexbarConfig:
    """Load config from TOML."""

    with path.open("rb") as config_file:
        data = tomllib.load(config_file)

    plex = data.get("plex", {})
    base_url = str(plex.get("base_url", "")).strip()
    token = str(plex.get("token", "")).strip()
    default_library = plex.get("default_library")

    if not base_url or not token:
        msg = f"Invalid Plexbar config at {path}: base_url and token are required"
        raise ValueError(msg)

    return PlexbarConfig(
        base_url=base_url,
        token=token,
        default_library=str(default_library) if default_library else None,
    )


def save_config(config: PlexbarConfig, path: Path = CONFIG_PATH) -> None:
    """Save config as TOML with user-only permissions where possible."""

    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "[plex]",
        f'base_url = "{_toml_escape(config.base_url)}"',
        f'token = "{_toml_escape(config.token)}"',
    ]
    if config.default_library:
        lines.append(f'default_library = "{_toml_escape(config.default_library)}"')
    contents = "\n".join(lines) + "\n"

    old_umask = os.umask(0o177)
    try:
        path.write_text(contents, encoding="utf-8")
    finally:
        os.umask(old_umask)
    path.chmod(0o600)


def _toml_escape(value: str) -> str:
    """Escape a value for a basic TOML string."""

    return value.replace("\\", "\\\\").replace('"', '\\"')
