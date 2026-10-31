import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(tempfile.mkdtemp(prefix="tubekeep-test-"))
os.environ.update(
    CONFIG_DIR=str(ROOT / "config"),
    DOWNLOADS_DIR=str(ROOT / "downloads"),
    TUBEKEEP_DISABLE_BACKGROUND="1",
    STATIC_DIR=str(ROOT / "static"),
)

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def make_audio(path: Path, fmt: str) -> Path:
    """1 second of silence in the given format."""
    codec = {"mp3": ["-c:a", "libmp3lame"], "m4a": ["-c:a", "aac"], "opus": ["-c:a", "libopus"],
             "flac": ["-c:a", "flac"]}[fmt]
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
                    "-t", "1", *codec, str(path)], check=True)
    return path


FAKE = r'''#!PYTHON
"""Stand-in for yt-dlp used by the tests. Reads its playlist from FAKE_YT_DIR/playlist.json."""
import json, os, shutil, sys
from pathlib import Path
d = Path(os.environ.get("FAKE_YT_DIR", "/tmp"))
a = sys.argv[1:]
if "--version" in a:
    print("2026.01.01"); sys.exit(0)
url = a[a.index("--") + 1] if "--" in a else a[-1]
if "-J" in a:
    if "private" in url:
        print("ERROR: [youtube:tab] PLprivate: This playlist is private", file=sys.stderr); sys.exit(1)
    if "list=WL" in url:
        if "--cookies" in a:
            print(json.dumps({"_type": "playlist", "id": "WL", "title": "Watch later", "entries": []})); sys.exit(0)
        print("ERROR: [youtube:tab] WL: The playlist does not exist", file=sys.stderr); sys.exit(1)
    pl = json.loads((d / "playlist.json").read_text())
    print(json.dumps(pl)); sys.exit(0)
vid = url.split("v=")[1][:11]
if vid.startswith("unavail"):
    print(f"ERROR: [youtube] {vid}: Video unavailable. This video has been removed", file=sys.stderr); sys.exit(1)
home = next(x.split(":", 1)[1] for i, x in enumerate(a) if a[i - 1] == "-P" and x.startswith("home:"))
tmpl = a[a.index("-o") + 1]
pl = json.loads((d / "playlist.json").read_text())
e = next(x for x in pl["entries"] if x["id"] == vid)
name = tmpl.replace("%(channel)s", e["channel"]).replace("%(title)s", e["title"]).replace("%(ext)s", "mp3")
out = Path(home) / name
out.parent.mkdir(parents=True, exist_ok=True)
shutil.copy(d / "silence.mp3", out)
from mutagen.id3 import ID3, TIT2, TPE1, TXXX
t = ID3(); t.add(TIT2(encoding=3, text=e["title"])); t.add(TPE1(encoding=3, text=e["channel"]))
t.add(TXXX(encoding=3, desc="purl", text=f"https://www.youtube.com/watch?v={vid}")); t.save(out)
print('TKPROG {"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100}', flush=True)
print("TKDONE " + json.dumps({"filepath": str(out), "id": vid, "title": e["title"], "channel": e["channel"],
                              "description": "desc", "artist": None, "upload_date": "20200102"}), flush=True)
'''


@pytest.fixture(scope="session")
def fake_yt():
    d = ROOT / "fake"
    d.mkdir(exist_ok=True)
    script = d / "yt-dlp"
    script.write_text(FAKE.replace("PYTHON", sys.executable))
    script.chmod(0o755)
    if HAS_FFMPEG:
        make_audio(d / "silence.mp3", "mp3")
    else:
        (d / "silence.mp3").write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 2000)
    os.environ["FAKE_YT_DIR"] = str(d)
    os.environ["BUNDLED_YTDLP"] = str(script)
    from app import config
    config.BUNDLED_YTDLP = script
    return d
