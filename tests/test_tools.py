import zipfile

import pytest

from grabbr import settings, tools


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GRABBR_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PATH", "")


def test_managed_copy_wins_over_path(tmp_path):
    exe = tools.managed_path("yt-dlp")
    exe.write_text("")
    assert tools.find("yt-dlp") == exe
    assert tools.is_managed(exe)


def test_missing_lists_required_and_optional():
    assert tools.missing() == ["yt-dlp", "ffmpeg", "deno"]
    assert tools.missing(include_optional=False) == ["yt-dlp", "ffmpeg"]


def test_extract_flattens_nested_members(tmp_path):
    archive = tmp_path / "ff.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("ffmpeg-master-latest-win64-gpl/bin/ffmpeg.exe", b"ff")
        zf.writestr("ffmpeg-master-latest-win64-gpl/bin/ffprobe.exe", b"fp")
        zf.writestr("ffmpeg-master-latest-win64-gpl/doc/readme.txt", b"x")
    out = tmp_path / "bin"
    out.mkdir()
    tools._extract(archive, ("bin/ffmpeg.exe", "bin/ffprobe.exe"), out)
    assert sorted(p.name for p in out.iterdir()) == ["ffmpeg.exe", "ffprobe.exe"]


def test_extract_complains_about_missing_member(tmp_path):
    archive = tmp_path / "d.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("other.exe", b"")
    with pytest.raises(tools.ToolError):
        tools._extract(archive, ("deno.exe",), tmp_path)


def test_settings_roundtrip_and_bad_values():
    s = settings.Settings(output_dir="X", audio_format="original", playlist=True)
    s.save()
    assert settings.Settings.load() == s
    settings.settings_file().write_text('{"audio_format": "flac", "junk": 1}')
    loaded = settings.Settings.load()
    assert loaded.audio_format == "mp3"
    settings.settings_file().write_text("not json")
    assert settings.Settings.load() == settings.Settings()
