"""Stand-in for yt-dlp: prints the tagged lines grabbr asks for.

Behaviour is picked by the URL:
  .../ok      -> progress, post-processing, one saved file, exit 0
  .../fail    -> an ERROR line, exit 1
  .../slow    -> keeps printing progress for a long time (for cancel tests)
"""

import sys
import time

args = sys.argv[1:]
url = args[-1]
out_dir = args[args.index("-P") + 1]

DL = "[grabbr:dl]"
PP = "[grabbr:pp]"
DONE = "[grabbr:done]"


def say(line: str) -> None:
    print(line, flush=True)


if url.endswith("/ok"):
    say("[youtube] Extracting URL: " + url)
    for done in (0, 500, 1000):
        say(f"{DL} {done}|1000|NA|2048.0|3|NA|NA|Test Song | Live")
    say(f"{PP} FFmpegExtractAudio|started|Test Song | Live")
    say(f"{PP} FFmpegExtractAudio|finished|Test Song | Live")
    say(f"{DONE} {out_dir}/Artist - Test Song.mp3")
    sys.exit(0)

if url.endswith("/orphan"):
    # Like ffmpeg/deno inheriting yt-dlp's stdout: a child keeps the pipe open
    # long after this process exits.
    import subprocess

    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], close_fds=False)
    say(f"{PP} FFmpegExtractAudio|started|Orphan")
    say(f"{DONE} {out_dir}/Orphan.mp3")
    sys.exit(0)

if url.endswith("/slowconvert"):
    for done in (0, 500, 1000):
        say(f"{DL} {done}|1000|NA|2048.0|3|NA|NA|Long Mix")
    say(f"{PP} FFmpegExtractAudio|started|Long Mix")
    time.sleep(4)
    say(f"{DONE} {out_dir}/Long Mix.mp3")
    sys.exit(0)

if url.endswith("/fail"):
    say("WARNING: something odd")
    say("ERROR: [youtube] abc: Video unavailable")
    sys.exit(1)

if url.endswith("/slow"):
    for i in range(600):
        say(f"{DL} {i}|NA|100000|NA|NA|1|3|Slow One")
        time.sleep(0.05)
    sys.exit(0)

sys.exit(2)
