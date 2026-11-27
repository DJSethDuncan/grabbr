"""Tiny JSON-backed settings store."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields

from grabbr.paths import default_output_dir, settings_file

AUDIO_FORMATS = ("mp3", "original")


@dataclass
class Settings:
    output_dir: str = str(default_output_dir())
    audio_format: str = "mp3"  # "mp3" or "original" (no re-encode)
    playlist: bool = False
    watch_clipboard: bool = True

    @classmethod
    def load(cls) -> Settings:
        path = settings_file()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        settings = cls(**{k: v for k, v in raw.items() if k in known})
        if settings.audio_format not in AUDIO_FORMATS:
            settings.audio_format = "mp3"
        return settings

    def save(self) -> None:
        try:
            settings_file().write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        except OSError:
            pass  # settings are a convenience; never crash over them
