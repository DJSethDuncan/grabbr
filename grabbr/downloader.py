"""Run yt-dlp as a subprocess and turn its output into events for the GUI.

We drive the yt-dlp executable rather than importing it as a library so it
can self-update (`yt-dlp -U`) without rebuilding grabbr. Machine-readable
progress comes from --progress-template / --print lines tagged with our own
prefixes; everything else is passed through as log text.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from grabbr import tools

DL_TAG = "[grabbr:dl]"
PP_TAG = "[grabbr:pp]"
DONE_TAG = "[grabbr:done]"

# Fields are '|'-separated; title goes last so a '|' in a title can't break parsing.
DL_TEMPLATE = (
    f"download:{DL_TAG} "
    "%(progress.downloaded_bytes)s|%(progress.total_bytes)s|"
    "%(progress.total_bytes_estimate)s|%(progress.speed)s|%(progress.eta)s|"
    "%(info.playlist_index)s|%(info.n_entries)s|%(info.title)s"
)
PP_TEMPLATE = f"postprocess:{PP_TAG} %(progress.postprocessor)s|%(progress.status)s|%(info.title)s"
DONE_TEMPLATE = f"after_move:{DONE_TAG} %(filepath)s"

OUTPUT_TEMPLATE = "%(artist,uploader|Unknown)s - %(title)s.%(ext)s"

_SUPPORTED_RE = re.compile(
    r"^https?://([a-z0-9-]+\.)*(youtube\.com|youtu\.be|soundcloud\.com)/\S+$",
    re.IGNORECASE,
)

_PP_LABELS = {
    "ExtractAudio": "Converting audio",
    "FFmpegExtractAudio": "Converting audio",
    "EmbedThumbnail": "Embedding artwork",
    "FFmpegMetadata": "Writing tags",
    "Metadata": "Writing tags",
    "ThumbnailsConvertor": "Preparing artwork",
    "FFmpegThumbnailsConvertor": "Preparing artwork",
    "MoveFiles": "Finishing up",
}


def is_supported_url(text: str) -> bool:
    """True for YouTube / SoundCloud links (used for clipboard auto-fill)."""
    return bool(_SUPPORTED_RE.match(text.strip()))


def looks_like_url(text: str) -> bool:
    text = text.strip()
    return text.startswith(("http://", "https://")) and " " not in text


# --- events -----------------------------------------------------------------


@dataclass
class Progress:
    fraction: float | None  # 0..1, None if size unknown
    downloaded: int | None
    total: int | None
    speed: float | None  # bytes/s
    eta: int | None  # seconds
    title: str
    index: int | None = None
    count: int | None = None


@dataclass
class Stage:
    text: str
    title: str = ""


@dataclass
class FileDone:
    path: str


@dataclass
class Log:
    text: str
    level: str = "info"  # "info" | "warning" | "error"


@dataclass
class Finished:
    ok: bool
    cancelled: bool = False
    files: list[str] = field(default_factory=list)
    error: str | None = None


Event = Progress | Stage | FileDone | Log | Finished


def _num(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:  # "NA", "None", ""
        return None


def _int(value: str) -> int | None:
    n = _num(value)
    return int(n) if n is not None else None


def parse_line(line: str) -> Event | None:
    line = line.rstrip("\r\n")
    if not line.strip():
        return None
    if line.startswith(DL_TAG):
        parts = line[len(DL_TAG) :].strip().split("|", 7)
        if len(parts) == 8:
            done, total, estimate, speed, eta, idx, count, title = parts
            downloaded = _int(done)
            size = _int(total) or _int(estimate)
            fraction = None
            if downloaded is not None and size:
                fraction = max(0.0, min(1.0, downloaded / size))
            return Progress(
                fraction=fraction,
                downloaded=downloaded,
                total=size,
                speed=_num(speed),
                eta=_int(eta),
                title=title,
                index=_int(idx),
                count=_int(count),
            )
    if line.startswith(PP_TAG):
        parts = line[len(PP_TAG) :].strip().split("|", 2)
        if len(parts) == 3:
            pp, status, title = parts
            if status == "started":
                return Stage(_PP_LABELS.get(pp, pp), title)
            return None  # "processing"/"finished" are noise
    if line.startswith(DONE_TAG):
        return FileDone(line[len(DONE_TAG) :].strip())
    if line.startswith("ERROR:"):
        return Log(line, "error")
    if line.startswith("WARNING:"):
        return Log(line, "warning")
    return Log(line)


# --- command ----------------------------------------------------------------


@dataclass
class DownloadOptions:
    output_dir: str
    audio_format: str = "mp3"  # "mp3" | "original"
    playlist: bool = False


def build_command(
    url: str,
    opts: DownloadOptions,
    ytdlp: list[str],
    ffmpeg: Path | None = None,
    deno: Path | None = None,
) -> list[str]:
    cmd = [*ytdlp, "--ignore-config", "-f", "bestaudio/best", "-x"]
    if opts.audio_format == "mp3":
        cmd += ["--audio-format", "mp3", "--audio-quality", "0"]  # V0 VBR
    else:
        cmd += ["--audio-format", "best"]  # keep source codec, no re-encode
    cmd += [
        "--embed-metadata",
        "--embed-thumbnail",
        "--convert-thumbnails",
        "jpg",
        "-P",
        opts.output_dir,
        "-o",
        OUTPUT_TEMPLATE,
        "--yes-playlist" if opts.playlist else "--no-playlist",
        "--no-simulate",
        "--progress",
        "--newline",
        "--no-colors",
        "--progress-template",
        DL_TEMPLATE,
        "--progress-template",
        PP_TEMPLATE,
        "--print",
        DONE_TEMPLATE,
    ]
    if ffmpeg is not None:
        cmd += ["--ffmpeg-location", str(ffmpeg.parent)]
    if deno is not None:
        cmd += ["--js-runtimes", f"deno:{deno}"]
    cmd += ["--", url]
    return cmd


# --- runner -----------------------------------------------------------------


class Download:
    """One yt-dlp run on a background thread. Events go to on_event (from that thread)."""

    def __init__(
        self,
        url: str,
        opts: DownloadOptions,
        on_event: Callable[[Event], None],
        ytdlp: list[str] | None = None,
    ) -> None:
        self.url = url.strip()
        self.opts = opts
        self.on_event = on_event
        self._ytdlp = ytdlp
        self._proc: subprocess.Popen[str] | None = None
        self._cancelled = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    def cancel(self) -> None:
        self._cancelled.set()
        proc = self._proc
        if proc and proc.poll() is None:
            if tools.IS_WINDOWS:
                # yt-dlp.exe is a launcher + child; kill the whole tree.
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    **tools.no_window_kwargs(),
                )
            else:
                proc.terminate()

    def _resolve_cmd(self) -> list[str]:
        ytdlp = self._ytdlp
        if ytdlp is None:
            path = tools.find("yt-dlp")
            if path is None:
                raise tools.ToolError("yt-dlp isn't installed. Click 'Set up tools'.")
            ytdlp = [str(path)]
        Path(self.opts.output_dir).mkdir(parents=True, exist_ok=True)
        return build_command(
            self.url, self.opts, ytdlp, ffmpeg=tools.find("ffmpeg"), deno=tools.find("deno")
        )

    def _run(self) -> None:
        files: list[str] = []
        errors: list[str] = []
        transcript = _Transcript()
        try:
            try:
                cmd = self._resolve_cmd()
                transcript.write("$ " + subprocess.list2cmdline(cmd))
                env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
                self._proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    env=env,
                    **tools.no_window_kwargs(),
                )
            except Exception as exc:  # tool missing, can't spawn, etc.
                transcript.write(f"!! failed to start: {exc!r}")
                self.on_event(Finished(ok=False, error=str(exc)))
                return

            def pump() -> None:
                assert self._proc is not None and self._proc.stdout is not None
                try:
                    for raw in self._proc.stdout:
                        transcript.write(raw.rstrip("\r\n"))
                        event = parse_line(raw)
                        if event is None:
                            continue
                        if isinstance(event, FileDone):
                            files.append(event.path)
                        elif isinstance(event, Log) and event.level == "error":
                            errors.append(event.text)
                        self.on_event(event)
                except Exception as exc:  # never let a bad line hang the job
                    transcript.write(f"!! reader error: {exc!r}")

            reader = threading.Thread(target=pump, daemon=True)
            reader.start()
            code = self._proc.wait()
            # A helper process (ffmpeg, deno) that inherited yt-dlp's stdout can keep
            # the pipe open after yt-dlp exits, so don't wait on EOF forever.
            reader.join(timeout=READER_GRACE_SECONDS)
            transcript.write(
                f"-- exit code {code}"
                + ("" if not reader.is_alive() else " (output pipe still open; continuing)")
            )

            if self._cancelled.is_set():
                self.on_event(Finished(ok=False, cancelled=True, files=list(files)))
            elif code == 0 and files:
                self.on_event(Finished(ok=True, files=list(files)))
            else:
                msg = errors[-1] if errors else f"yt-dlp exited with code {code}"
                if code == 0 and not files:
                    msg = "Nothing was downloaded."
                self.on_event(Finished(ok=False, files=list(files), error=msg))
        finally:
            transcript.close()


READER_GRACE_SECONDS = 3.0


class _Transcript:
    """Raw yt-dlp output for the latest run, in %LOCALAPPDATA%\\grabbr\\last-run.log."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        try:
            from grabbr.paths import data_dir

            self._fh = open(data_dir() / "last-run.log", "w", encoding="utf-8")  # noqa: SIM115
        except OSError:
            self._fh = None

    def write(self, line: str) -> None:
        with self._lock:
            if self._fh:
                try:
                    self._fh.write(f"{time.strftime('%H:%M:%S')} {line}\n")
                    self._fh.flush()
                except (OSError, ValueError):
                    pass

    def close(self) -> None:
        with self._lock:
            if self._fh:
                self._fh.close()
                self._fh = None
