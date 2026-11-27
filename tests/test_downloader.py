import sys
import time
from pathlib import Path

import pytest

from grabbr import downloader as d

FAKE = [sys.executable, str(Path(__file__).with_name("fake_ytdlp.py"))]


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GRABBR_HOME", str(tmp_path / "home"))


def run(url: str, tmp_path: Path, cancel_after: float | None = None) -> list:
    events: list = []
    opts = d.DownloadOptions(output_dir=str(tmp_path / "out"))
    job = d.Download(url, opts, events.append, ytdlp=FAKE)
    job.start()
    if cancel_after is not None:
        time.sleep(cancel_after)
        job.cancel()
    job.join(timeout=20)
    return events


def test_supported_urls():
    assert d.is_supported_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert d.is_supported_url("https://youtu.be/dQw4w9WgXcQ")
    assert d.is_supported_url("https://music.youtube.com/watch?v=abc")
    assert d.is_supported_url("https://soundcloud.com/artist/track")
    assert d.is_supported_url("https://on.soundcloud.com/AbC123")
    assert not d.is_supported_url("https://example.com/watch?v=abc")
    assert not d.is_supported_url("youtube.com/watch?v=abc")
    assert not d.is_supported_url("https://evilyoutube.com/x")


def test_parse_progress_keeps_pipes_in_title():
    ev = d.parse_line(f"{d.DL_TAG} 250|1000|NA|1024.5|7|2|10|A | B")
    assert isinstance(ev, d.Progress)
    assert ev.fraction == 0.25
    assert ev.title == "A | B"
    assert (ev.index, ev.count, ev.eta) == (2, 10, 7)


def test_parse_progress_falls_back_to_estimate_and_handles_na():
    ev = d.parse_line(f"{d.DL_TAG} 50|NA|200|NA|NA|NA|NA|x")
    assert ev.fraction == 0.25 and ev.speed is None and ev.index is None
    ev = d.parse_line(f"{d.DL_TAG} 50|NA|NA|NA|NA|NA|NA|x")
    assert ev.fraction is None and ev.downloaded == 50


def test_parse_stage_and_levels():
    assert d.parse_line(f"{d.PP_TAG} FFmpegExtractAudio|started|t") == d.Stage(
        "Converting audio", "t"
    )
    assert d.parse_line(f"{d.PP_TAG} FFmpegExtractAudio|finished|t") is None
    assert d.parse_line("ERROR: nope").level == "error"
    assert d.parse_line("WARNING: hm").level == "warning"
    assert d.parse_line("   ") is None


def test_build_command_mp3():
    opts = d.DownloadOptions(output_dir="C:/Music", audio_format="mp3", playlist=False)
    cmd = d.build_command(
        "https://youtu.be/x", opts, ["yt-dlp"], ffmpeg=Path("/b/ffmpeg"), deno=Path("/b/deno")
    )
    assert cmd[0] == "yt-dlp"
    assert cmd[-2:] == ["--", "https://youtu.be/x"]
    assert cmd[cmd.index("--audio-format") + 1] == "mp3"
    assert "--no-playlist" in cmd
    assert cmd[cmd.index("--ffmpeg-location") + 1] == str(Path("/b"))
    assert cmd[cmd.index("--js-runtimes") + 1] == f"deno:{Path('/b/deno')}"


def test_build_command_original_playlist_no_tools():
    opts = d.DownloadOptions(output_dir="out", audio_format="original", playlist=True)
    cmd = d.build_command("https://soundcloud.com/a/sets/b", opts, ["yt-dlp"])
    assert cmd[cmd.index("--audio-format") + 1] == "best"
    assert "--yes-playlist" in cmd
    assert "--ffmpeg-location" not in cmd and "--js-runtimes" not in cmd


def test_successful_run(tmp_path):
    events = run("https://youtu.be/ok", tmp_path)
    progress = [e for e in events if isinstance(e, d.Progress)]
    assert [p.fraction for p in progress] == [0.0, 0.5, 1.0]
    assert any(isinstance(e, d.Stage) for e in events)
    final = events[-1]
    assert isinstance(final, d.Finished) and final.ok
    assert final.files == [f"{tmp_path / 'out'}/Artist - Test Song.mp3"]
    assert (tmp_path / "out").is_dir()


def test_failed_run_reports_last_error(tmp_path):
    final = run("https://youtu.be/fail", tmp_path)[-1]
    assert isinstance(final, d.Finished) and not final.ok
    assert "Video unavailable" in final.error


def test_cancel(tmp_path):
    final = run("https://youtu.be/slow", tmp_path, cancel_after=0.5)[-1]
    assert isinstance(final, d.Finished) and final.cancelled and not final.ok


def test_missing_ytdlp_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(d.tools, "find", lambda name: None)
    events: list = []
    job = d.Download("https://youtu.be/ok", d.DownloadOptions(str(tmp_path)), events.append)
    job.start()
    job.join(timeout=5)
    assert isinstance(events[-1], d.Finished) and "yt-dlp" in events[-1].error
