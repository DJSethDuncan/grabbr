"""Where grabbr keeps its settings and helper binaries."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "grabbr"


def data_dir() -> Path:
    """Per-user app data folder (%LOCALAPPDATA%\\grabbr on Windows).

    Set GRABBR_HOME to override (handy for tests and portable installs).
    """
    override = os.environ.get("GRABBR_HOME")
    if override:
        path = Path(override)
    elif sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        path = Path(base) / APP_NAME
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def bin_dir() -> Path:
    """Folder holding grabbr-managed yt-dlp / ffmpeg / deno binaries."""
    path = data_dir() / "bin"
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_file() -> Path:
    return data_dir() / "settings.json"


def default_output_dir() -> Path:
    return Path.home() / "Music" / APP_NAME
