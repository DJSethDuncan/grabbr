"""Find, install and update the external tools grabbr drives.

grabbr keeps its own copies in paths.bin_dir() and prefers them over
anything on PATH:

* yt-dlp  - the downloader (official build; includes yt-dlp-ejs)
* ffmpeg  - audio extraction / MP3 conversion / thumbnail embedding
* deno    - JavaScript runtime yt-dlp needs to solve YouTube's JS challenges

Automatic install is Windows-only; elsewhere, install them with your
package manager and grabbr will pick them up from PATH.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from grabbr.paths import bin_dir

IS_WINDOWS = sys.platform == "win32"
EXE_SUFFIX = ".exe" if IS_WINDOWS else ""

LogFn = Callable[[str], None]
ProgressFn = Callable[[float | None], None]  # 0..1, or None if unknown


class ToolError(Exception):
    pass


@dataclass(frozen=True)
class Tool:
    name: str
    url: str  # Windows x64 download
    zip_members: tuple[str, ...] = ()  # files to pull out of a zip; empty = url is the exe
    version_args: tuple[str, ...] = ("--version",)
    required: bool = True
    why: str = ""


TOOLS: dict[str, Tool] = {
    "yt-dlp": Tool(
        name="yt-dlp",
        url="https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe",
        why="the downloader",
    ),
    "ffmpeg": Tool(
        name="ffmpeg",
        url="https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/"
        "ffmpeg-master-latest-win64-gpl.zip",
        zip_members=("bin/ffmpeg.exe", "bin/ffprobe.exe"),
        version_args=("-version",),
        why="converts audio to MP3 and embeds artwork",
    ),
    "deno": Tool(
        name="deno",
        url="https://github.com/denoland/deno/releases/latest/download/"
        "deno-x86_64-pc-windows-msvc.zip",
        zip_members=("deno.exe",),
        required=False,  # SoundCloud works without it; YouTube mostly doesn't
        why="JavaScript runtime YouTube downloads need",
    ),
}


def no_window_kwargs() -> dict:
    """Stop console windows flashing up when a GUI app spawns a subprocess."""
    if IS_WINDOWS:
        return {"creationflags": subprocess.CREATE_NO_WINDOW}  # type: ignore[attr-defined]
    return {}


def managed_path(name: str) -> Path:
    return bin_dir() / f"{name}{EXE_SUFFIX}"


def find(name: str) -> Path | None:
    """Return the tool to use: grabbr's own copy first, then PATH."""
    managed = managed_path(name)
    if managed.is_file():
        return managed
    found = shutil.which(name)
    return Path(found) if found else None


def is_managed(path: Path) -> bool:
    try:
        return path.resolve().parent == bin_dir().resolve()
    except OSError:
        return False


def missing(include_optional: bool = True) -> list[str]:
    return [
        name
        for name, tool in TOOLS.items()
        if (tool.required or include_optional) and find(name) is None
    ]


def version(name: str) -> str | None:
    path = find(name)
    if path is None:
        return None
    try:
        out = subprocess.run(
            [str(path), *TOOLS[name].version_args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown version"
    first = (out.stdout or out.stderr).strip().splitlines()
    return first[0] if first else "unknown version"


def install(name: str, log: LogFn = print, progress: ProgressFn | None = None) -> Path:
    """Download a tool into grabbr's bin folder."""
    tool = TOOLS[name]
    if not IS_WINDOWS:
        raise ToolError(
            f"Automatic install only works on Windows. Install {name} with your "
            "package manager and make sure it's on PATH."
        )
    dest_dir = bin_dir()
    log(f"Downloading {name} from {tool.url}")
    if not tool.zip_members:
        target = managed_path(name)
        _download(tool.url, target, progress)
    else:
        archive = dest_dir / f"{name}.download.zip"
        try:
            _download(tool.url, archive, progress)
            log(f"Extracting {name}…")
            _extract(archive, tool.zip_members, dest_dir)
        finally:
            archive.unlink(missing_ok=True)
        target = managed_path(name)
    if not target.is_file():
        raise ToolError(f"{name} download finished but {target.name} wasn't found")
    log(f"Installed {name} → {target}")
    return target


def update_ytdlp(log: LogFn = print) -> None:
    """Self-update grabbr's yt-dlp, or install a managed copy if it uses PATH's."""
    path = find("yt-dlp")
    if path is None or not is_managed(path):
        if IS_WINDOWS:
            install("yt-dlp", log)
            return
        if path is None:
            raise ToolError("yt-dlp isn't installed")
        log(f"Using yt-dlp from {path}; update it with the tool you installed it with.")
        return
    proc = subprocess.Popen(
        [str(path), "-U"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        **no_window_kwargs(),
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        if line.strip():
            log(line.rstrip())
    if proc.wait() != 0:
        raise ToolError("yt-dlp update failed (see log)")


def _download(url: str, dest: Path, progress: ProgressFn | None) -> None:
    tmp = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "grabbr"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as fh:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while chunk := resp.read(1 << 16):
                fh.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done / total if total else None)
        tmp.replace(dest)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise ToolError(f"Download failed: {exc}") from exc


def _extract(archive: Path, members: tuple[str, ...], dest_dir: Path) -> None:
    """Pull specific files out of a zip, flattening them into dest_dir."""
    with zipfile.ZipFile(archive) as zf:
        wanted = set(members)
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            for member in list(wanted):
                if name == member or name.endswith("/" + member):
                    target = dest_dir / Path(member).name
                    with zf.open(info) as src, open(target, "wb") as out:
                        shutil.copyfileobj(src, out)
                    wanted.discard(member)
        if wanted:
            raise ToolError(f"Archive is missing: {', '.join(sorted(wanted))}")
