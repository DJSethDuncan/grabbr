# grabbr

Get files online locally. Paste a YouTube or SoundCloud link, get an MP3, with tags and cover art embedded.

A small Windows desktop app that wraps [yt-dlp](https://github.com/yt-dlp/yt-dlp) and [ffmpeg](https://ffmpeg.org/).

## Features

- Paste a link (or just copy one: grabbr auto-fills YouTube/SoundCloud links from the clipboard), hit **Grab**
- **MP3** (V0 VBR, the best MP3 quality) or **Original** (keeps the source Opus/M4A stream, no re-encode)
- Title/artist tags and thumbnail artwork embedded, saved as `Artist - Title.mp3`
- Optional whole-playlist / SoundCloud-set downloads
- Progress, speed, ETA, cancel, and a log of what happened
- **Update yt-dlp** button for when YouTube changes something and downloads start failing

## Install

1. Download `grabbr.exe` from the [latest release](https://github.com/DJSethDuncan/grabbr/releases/latest).
2. Run it. Windows SmartScreen may warn because the exe isn't code-signed: **More info → Run anyway**.
3. On first launch grabbr offers to download the tools it needs (about 150–200 MB, one time, all from their official GitHub releases):

| Tool | Why |
|---|---|
| `yt-dlp.exe` | the downloader |
| `ffmpeg.exe` / `ffprobe.exe` | MP3 conversion, tags, artwork |
| `deno.exe` | JavaScript runtime yt-dlp needs to solve YouTube's JS challenges |

They live in `%LOCALAPPDATA%\grabbr\bin` and take priority over anything on your PATH. Settings are in `%LOCALAPPDATA%\grabbr\settings.json`. Files save to `Music\grabbr` by default.

### When downloads break

YouTube changes things regularly. If YouTube downloads start failing (403 errors, "Sign in to confirm", missing formats), click **Update yt-dlp**. That fixes it nearly every time.

## How it works

grabbr drives the `yt-dlp` **executable** as a subprocess instead of importing it as a Python library. That way yt-dlp can update itself (`yt-dlp -U`) without a new grabbr build, and grabbr itself stays small. Machine-readable progress comes from yt-dlp's `--progress-template` / `--print` options with grabbr-specific tags; see `grabbr/downloader.py`.

```
grabbr/
  app.py         CustomTkinter window
  downloader.py  builds the yt-dlp command, runs it, parses progress into events
  tools.py       finds / installs / updates yt-dlp, ffmpeg, deno
  settings.py    JSON settings
  paths.py       %LOCALAPPDATA%\grabbr locations
tests/           pytest suite (uses a fake yt-dlp, so no network needed)
```

## Development

Requires Python 3.10+.

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt

python -m grabbr          # run from source
python -m pytest          # tests
ruff check . ; ruff format .
.\build.ps1               # build dist\grabbr.exe locally
```

On macOS/Linux the app runs from source too, but automatic tool install is Windows-only: install `yt-dlp`, `ffmpeg` and `deno` with your package manager.

## Releases

GitHub Actions (`.github/workflows/build.yml`) runs lint and tests on Windows and Ubuntu, and builds `grabbr.exe` on every push. The exe is attached as a workflow artifact. To publish a release:

```bash
git tag v0.1.0
git push origin v0.1.0
```

The workflow builds the exe and attaches it to a GitHub Release for that tag.

## A note on rights

Downloading from YouTube is against YouTube's Terms of Service, and copyright law applies to what you download. Use grabbr for things you have the right to keep: your own uploads, Creative Commons or public-domain material, or tracks the artist has made available for download.
